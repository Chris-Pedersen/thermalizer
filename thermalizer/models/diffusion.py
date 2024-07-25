import torch.nn as nn
import torch
import math
from tqdm import tqdm
from scipy.stats import truncnorm
import torch
        
        
class Diffusion(nn.Module):
    def __init__(self,config,model,silence=True):
        """ Pass the CNN architecture as a model object.
        Silence disables tqdm bar during sampling (to not pollute slurm logfiles) """
        
        super().__init__()
        self.config=config
        if "timesteps" in self.config:
            self.timesteps=self.config["timesteps"]
        self.in_channels=self.config["input_channels"]
        self.image_size=self.config["image_size"]
        if "noise_sampling_coeff" in self.config:
            self.noise_sampling_coeff=config["noise_sampling_coeff"]
        else:
            self.noise_sampling_coeff=None

        if "whitening" in self.config:
            if self.config["whitening"]:
                whitening_transform=torch.load(self.config["whitening"])
                self.register_buffer("whitening_transform",whitening_transform)
            else:
                self.whitening_transform=None
        else:
            self.whitening_transform=None
            
        self.silence=silence
        self.sampled_times=[]
        

        betas=self._cosine_variance_schedule(self.timesteps)

        alphas=1.-betas
        alphas_cumprod=torch.cumprod(alphas,dim=-1)

        self.register_buffer("betas",betas)
        self.register_buffer("alphas",alphas)
        self.register_buffer("alphas_cumprod",alphas_cumprod)
        self.register_buffer("sqrt_alphas_cumprod",torch.sqrt(alphas_cumprod))
        self.register_buffer("sqrt_one_minus_alphas_cumprod",torch.sqrt(1.-alphas_cumprod))
        
        self.model=model

    def forward(self,x,noise):
        # x:NCHW
        ## Either uniform noise sampling, or selectively closer to 0
        ## here we use the absolute magnitude of a truncated normal with mean 0
        if self.noise_sampling_coeff:
            ## Draw from [0,1]
            t=torch.tensor(abs(truncnorm(a=-1/self.noise_sampling_coeff, b=1/self.noise_sampling_coeff,
                                            scale=self.noise_sampling_coeff).rvs(size=(x.shape[0],))))
            ## Normalise to full span of timestep range, and convert to int
            t=t*self.timesteps
            t=t.to(torch.int64).to(x.device)
        else:
            t=torch.randint(0,self.timesteps,(x.shape[0],)).to(x.device)
        self.sampled_times.append(t)

        ## Whiten the input data if we are whitening
        if self.whitening_transform is not None:
            x=self.whiten_batch(x)
        x_t=self._forward_diffusion(x,t,noise)
        pred_noise=self.model(x_t,t)

        return pred_noise,x_t,t

    def whiten_batch(self,batch):
        """ For a given batch of fields, whiten using the whitening transformation """

        ## First vectorise batch
        batch=batch.reshape((batch.shape[0],batch.shape[1],self.config["image_size"]**2))
        ## Whiten
        batch=torch.matmul(batch,self.whitening_transform[0].real)
        ## Reshape back to image dimensions
        batch=batch.reshape((batch.shape[0],batch.shape[1],self.config["image_size"],self.config["image_size"]))
        return batch

    def dewhiten_batch(self,batch):
        """ For a given batch of fields, dewhiten using the whitening transformation """

        ## First vectorise batch
        batch=batch.reshape((batch.shape[0],batch.shape[1],self.config["image_size"]**2))
        ## Dewhiten using inverse transform, K^{1/2}
        batch=torch.matmul(batch,self.whitening_transform[1].real)
        ## Reshape back to image dimensions
        batch=batch.reshape((batch.shape[0],batch.shape[1],self.config["image_size"],self.config["image_size"]))
        return batch

    @torch.no_grad()
    def sampling(self,n_samples,clipped_reverse_diffusion=None,device="cuda"):
        """ Generate fresh samples from pure noise """
        x_t=torch.randn((n_samples,self.in_channels,self.image_size,self.image_size)).to(device)
        for i in tqdm(range(self.timesteps-1,-1,-1),desc="Sampling",disable=self.silence):
            noise=torch.randn_like(x_t).to(device)
            t=torch.tensor([i for _ in range(n_samples)]).to(device)
            if clipped_reverse_diffusion is not None:
                x_t=self._reverse_diffusion_with_clip(x_t,t,noise,clipped_reverse_diffusion)
            else:
                x_t=self._reverse_diffusion(x_t,t,noise)

        return x_t

    @torch.no_grad()
    def denoising(self,x,denoising_timestep,device="cuda"):
        """ Pass validation samples, x, and some denoising timestep.
            Add noise using forward diffusion, denoise these samples and return
            both the forward diffused and denoised images, after dewhitening if
            we are doing whitening """

        if self.whitening_transform is not None:
            x=self.whiten_batch(x)

        ## Noise timestep
        t=(torch.ones(len(x),dtype=torch.int64)*denoising_timestep).to(device)
        noise=torch.randn_like(x).to(device)
        noised=self._forward_diffusion(x,t,noise)
        
        x_t=noised.to(device)
        for i in tqdm(range(denoising_timestep-1,-1,-1),desc="Denoising",disable=self.silence):
            noise=torch.randn_like(x_t).to(device)
            t=torch.tensor([i for _ in range(len(x))]).to(device)
            x_t=self._reverse_diffusion(x_t,t,noise)

        ## Dewhiten if we are whitening
        if self.whitening_transform is not None:
            ## The "denoised" fields
            x_t=self.dewhiten_batch(x_t)
            ## And the forward-diffused fields
            noised=self.dewhiten_batch(noised)
            
        return x_t, noised
    
    def _cosine_variance_schedule(self,timesteps,epsilon= 0.008):
        steps=torch.linspace(0,timesteps,steps=timesteps+1,dtype=torch.float32)
        f_t=torch.cos(((steps/timesteps+epsilon)/(1.0+epsilon))*math.pi*0.5)**2
        betas=torch.clip(1.0-f_t[1:]/f_t[:timesteps],0.0,0.999)

        return betas

    def _forward_diffusion(self,x_0,t,noise):
        assert x_0.shape==noise.shape
        #q(x_{t}|x_{0})
        return self.sqrt_alphas_cumprod.gather(-1,t).reshape(x_0.shape[0],1,1,1)*x_0+ \
                self.sqrt_one_minus_alphas_cumprod.gather(-1,t).reshape(x_0.shape[0],1,1,1)*noise

    @torch.no_grad()
    def _reverse_diffusion(self,x_t,t,noise):
        '''
        p(x_{t-1}|x_{t})-> mean,std

        pred_noise-> pred_mean and pred_std
        '''
        pred=self.model(x_t,t)

        alpha_t=self.alphas.gather(-1,t).reshape(x_t.shape[0],1,1,1)
        alpha_t_cumprod=self.alphas_cumprod.gather(-1,t).reshape(x_t.shape[0],1,1,1)
        beta_t=self.betas.gather(-1,t).reshape(x_t.shape[0],1,1,1)
        sqrt_one_minus_alpha_cumprod_t=self.sqrt_one_minus_alphas_cumprod.gather(-1,t).reshape(x_t.shape[0],1,1,1)
        mean=(1./torch.sqrt(alpha_t))*(x_t-((1.0-alpha_t)/sqrt_one_minus_alpha_cumprod_t)*pred)

        if t.min()>0:
            alpha_t_cumprod_prev=self.alphas_cumprod.gather(-1,t-1).reshape(x_t.shape[0],1,1,1)
            std=torch.sqrt(beta_t*(1.-alpha_t_cumprod_prev)/(1.-alpha_t_cumprod))
        else:
            std=0.0

        return mean+std*noise 

    @torch.no_grad()
    def _reverse_diffusion_with_clip(self,x_t,t,noise,clamp=1.): 
        '''
        p(x_{0}|x_{t}),q(x_{t-1}|x_{0},x_{t})->mean,std

        pred_noise -> pred_x_0 (clip to [-1.0,1.0]) -> pred_mean and pred_std
        '''
        pred=self.model(x_t,t)
        alpha_t=self.alphas.gather(-1,t).reshape(x_t.shape[0],1,1,1)
        alpha_t_cumprod=self.alphas_cumprod.gather(-1,t).reshape(x_t.shape[0],1,1,1)
        beta_t=self.betas.gather(-1,t).reshape(x_t.shape[0],1,1,1)
        
        x_0_pred=torch.sqrt(1. / alpha_t_cumprod)*x_t-torch.sqrt(1. / alpha_t_cumprod - 1.)*pred
        x_0_pred.clamp_(-clamp,clamp)

        if t.min()>0:
            alpha_t_cumprod_prev=self.alphas_cumprod.gather(-1,t-1).reshape(x_t.shape[0],1,1,1)
            mean= (beta_t * torch.sqrt(alpha_t_cumprod_prev) / (1. - alpha_t_cumprod))*x_0_pred +\
                 ((1. - alpha_t_cumprod_prev) * torch.sqrt(alpha_t) / (1. - alpha_t_cumprod))*x_t

            std=torch.sqrt(beta_t*(1.-alpha_t_cumprod_prev)/(1.-alpha_t_cumprod))
        else:
            mean=(beta_t / (1. - alpha_t_cumprod))*x_0_pred #alpha_t_cumprod_prev=1 since 0!=1
            std=0.0

        return mean+std*noise 
