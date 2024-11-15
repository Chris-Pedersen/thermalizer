import matplotlib.animation as animation
from IPython.display import HTML

import thermalizer.models.misc as misc
import thermalizer.kolmogorov.simulate as simulate
import thermalizer.kolmogorov.performance as performance
import thermalizer.kolmogorov.util as util
import torch
import torch.nn as nn
import seaborn as sns
import pickle
import matplotlib.pyplot as plt
from tqdm import tqdm
import math
import numpy as np
import os
import sys
import json
import time
import wandb


def therm_inference(identifier,start,stop,steps,forward_diff,project="therm_tests"):

    #wandb.init(entity="chris-pedersen",project="therm_sweep",dir="/scratch/cp3759/thermalizer_data/wandb_data")

    config={}
    config["save_dir"]="/scratch/cp3759/thermalizer_data/test_therms"
    config["identifier"]="sweep_test"
    config["save_string"]=config["save_dir"]+"/"+config["identifier"]
    config["start"]=wandb.config.start
    config["stop"]=wandb.config.stop
    config["steps"]=steps
    config["forward_diff"]=True
    config["emulator"]="/scratch/cp3759/thermalizer_data/wandb_data/wandb/run-20240804_230341-06kgy1hz/files/model_weights.pt"
    config["thermalizer"]="/scratch/cp3759/pyqg_data/wandb_runs/wandb/run-20241022_210436-180aqx69/files/model_weights.pt"

    #save_dir=sys.argv[1]
    #start=int(sys.argv[2])
    #stop=int(sys.argv[3])

    print("Saving results in directory %s" % config["save_string"])
    os.system(f'mkdir -p {config["save_string"]}')

    print("Save path =",config["save_string"])
    print("Therm start =",config["start"])
    print("Therm stop =",config["stop"])

    model_emu=misc.load_model(config["emulator"]) ## 4 step DRN, big batch, kinda shit
    model_emu=model_emu.to("cuda")
    model_emu=model_emu.eval()

    ## Load test data
    with open("/scratch/cp3759/thermalizer_data/kolmogorov/reynolds10k/test40.p", 'rb') as fp:
        test_suite = pickle.load(fp)

    ## Do the normalisation once - everything is done in normalised space here
    test_suite["data"]/=model_emu.config["field_std"]
    #test_suite["data"]=test_suite["data"][:,:15000,:,:] ## Cut the data down a bit
    ## Set some initial test fields

    ## Get true enstrophys
    ens_true=abs(test_suite["data"]**2).sum(axis=(2,3))

    model_therm=misc.load_diffusion_model(config["thermalizer"])
    model_therm=model_therm.eval()
    model_therm=model_therm.to("cuda")


    config["emulator_url"]=model_emu.config["wandb_url"]
    config["thermalizer_url"]=model_therm.config["wandb_url"]

    #wandb.init(project=project, entity="chris-pedersen",config=config,dir=config["save_string"])
    wandb.config.update(config)

    ## So first step is to thermalize according to the predicted timestep
    softmax = nn.Softmax(dim=1)
    grid=util.fourierGrid(64)

    ## Run emulator
    emu=performance.run_emu(test_suite["data"][:,0,:,:],model_emu,model_therm,config["steps"],silent=True)

    ## Classify noise levels on tru sim just for continuity
    noise_classes_sim=torch.zeros(len(emu[0]),len(emu[0][1]))
    for aa in range(len(emu[0][1])):
        preds=model_therm.model.noise_class(test_suite["data"][:,aa].unsqueeze(1).to("cuda"))
        noise_classes_sim[:,aa]=preds.cpu()

    ## Run thermalizer algorithm
    start = time.time()
    algo=performance.therm_algo_2(test_suite["data"][:,0,:,:],model_emu,model_therm,config["steps"],config["start"],config["stop"],forward=config["forward_diff"],silent=True)
    end = time.time()
    algo_time=end-start
    print("Algo time =", algo_time)

    ## Enstrophy figure
    enstrophy_figure=plt.figure()
    plt.title("Enstrophy from long emulator rollout (Thermalized in blue, true in red)")
    for aa in range(len(ens_true)):
        plt.plot(algo[1][aa].cpu(),color="blue",alpha=0.4)
        plt.plot(ens_true[aa][:len(algo[1][aa])].cpu(),color="red",alpha=0.4)
        #plt.plot(true_ens[aa],color="blue",alpha=0.4)
    plt.ylim(1000,10000)
    plt.xlabel("Emulator timestep")
    plt.ylabel("Enstrophy")
    wandb.log({"Enstrophy": wandb.Image(enstrophy_figure)})
    plt.close()

    ## Ticker tape figure
    ticker=plt.figure(figsize=(20,4))
    plt.subplot(2,1,1)
    plt.title("Therm steps ticker tape; first 500")
    plt.imshow(algo[-1][:,:500])
    plt.colorbar()
    plt.subplot(2,1,2)
    plt.title("Therm steps ticker tape; last 500")
    plt.imshow(algo[-1][:,-500:])
    plt.colorbar()
    plt.tight_layout()
    wandb.log({"Ticker tape": wandb.Image(ticker)})
    plt.close()

    ## Therm steps, full counter
    indices=[1,2,3,4,5]
    steps_full=plt.figure(figsize=(20,10))
    plt.suptitle("Thermalizing steps, full run")
    for idx in range(1,len(indices)+1):
        plt.subplot(len(indices),1,idx)
        plt.title("%d" % algo[-1][idx].sum().item())
        plt.plot(algo[-1][idx])
    wandb.log({"Therm steps full": wandb.Image(steps_full)})
    plt.close()

    ## Therm steps, zoomed
    steps_zoom=plt.figure(figsize=(20,10))
    plt.suptitle("Thermalizing steps, last 1000")
    for idx in range(1,len(indices)+1):
        plt.subplot(len(indices),1,idx)
        plt.title("%d" % algo[-1][idx][-1000:].sum().item())
        plt.plot(algo[-1][idx][-1000:])
    wandb.log({"Therm steps zoom": wandb.Image(steps_zoom)})
    plt.close()

    ## KE figure
    ke_steps=12
    ke_indices=np.linspace(1,config["steps"]-1,ke_steps,dtype=int)
    ## Now get KE and plot
    ## Get spectra to confirm
    ke_ic=util.get_ke_batch(test_suite["data"][:,0],grid)
    ke_figure=plt.figure(figsize=(14,4.5))
    for aa in range(1,len(ke_indices)+1):
        plt.subplot(2,6,aa)
        ke_emu=util.get_ke_batch(emu[0][:,ke_indices[aa-1]],grid)
        ke_therm=util.get_ke_batch(algo[0][:,ke_indices[aa-1]],grid)
        spec_sim,nans=util.spectral_similarity(ke_ic[1],ke_therm[1])
        plt.title("Step %d, ss=%.1f" % (ke_indices[aa-1],spec_sim))
        for bb in range(len(ke_ic[1])):
            plt.loglog(ke_emu[0],ke_emu[1][bb],color="gray",alpha=0.3)
            plt.loglog(ke_therm[0],ke_therm[1][bb],color="blue",alpha=0.3)
            plt.loglog(ke_ic[0],ke_ic[1][bb],color="red",alpha=0.1)
        plt.ylim(1e1,3e7)
        plt.xlim(7e-1,3.5e1)
    plt.tight_layout()
    wandb.log({"Kinetic energies": wandb.Image(ke_figure)})
    plt.close()

    ## Field figures
    field_figure1=plt.figure(figsize=(20,12))
    with torch.no_grad():
        preds=model_therm.model.noise_class(algo[0][:,len(algo[0][0])//2].unsqueeze(1).to("cuda"))
    plt.suptitle("Thermalized states, halfway point, step %d" % (len(algo[0][0])//2))
    for aa in range(1,(5*7)+1):
        plt.subplot(5,7,aa)
        plt.title("%d" % preds[aa].cpu())
        plt.imshow(algo[0][aa,len(algo[0][0])//2].cpu().squeeze(),cmap=sns.cm.icefire)
        plt.colorbar()
        plt.xticks([])
        plt.yticks([])
    plt.tight_layout()
    wandb.log({"Fields half": wandb.Image(field_figure1)})
    plt.close()

    ## Field figures2
    field_figure2=plt.figure(figsize=(20,12))
    with torch.no_grad():
        preds=model_therm.model.noise_class(algo[0][:,-1].unsqueeze(1).to("cuda"))
    plt.suptitle("Thermalized states, final point, step %d" % (len(algo[0][0])))
    for aa in range(1,(5*7)+1):
        plt.subplot(5,7,aa)
        plt.title("%d" % preds[aa].cpu())
        plt.imshow(algo[0][aa,-1].cpu().squeeze(),cmap=sns.cm.icefire)
        plt.colorbar()
        plt.xticks([])
        plt.yticks([])
    plt.tight_layout()
    wandb.log({"Fields final": wandb.Image(field_figure2)})
    plt.close()

    ## Field figures for emulator
    field_figure1_emu=plt.figure(figsize=(20,12))
    with torch.no_grad():
        preds=model_therm.model.noise_class(emu[0][:,len(emu[0][0])//2].unsqueeze(1).to("cuda"))
    plt.suptitle("Emulator only, halfway point, step %d" % (len(emu[0][0])//2))
    for aa in range(1,(5*7)+1):
        plt.subplot(5,7,aa)
        plt.title("%d" % preds[aa].cpu())
        plt.imshow(emu[0][aa,len(emu[0][0])//2].cpu().squeeze(),cmap=sns.cm.icefire)
        plt.colorbar()
        plt.xticks([])
        plt.yticks([])
    plt.tight_layout()
    wandb.log({"Fields half emu": wandb.Image(field_figure1_emu)})
    plt.close()

    ## Field figures for emulator, final
    field_figure2_emu=plt.figure(figsize=(20,12))
    with torch.no_grad():
        preds=model_therm.model.noise_class(emu[0][:,-1].unsqueeze(1).to("cuda"))
    plt.suptitle("Emulator only, final point, step %d" % (len(emu[0][0])))
    for aa in range(1,(5*7)+1):
        plt.subplot(5,7,aa)
        plt.title("%d" % preds[aa].cpu())
        plt.imshow(emu[0][aa,-1].cpu().squeeze(),cmap=sns.cm.icefire)
        plt.colorbar()
        plt.xticks([])
        plt.yticks([])
    plt.tight_layout()
    wandb.log({"Fields final emu": wandb.Image(field_figure2_emu)})
    plt.close()

    ## Noise class figure
    fig_noise_classes=plt.figure(figsize=(18,4))
    plt.subplot(1,3,1)
    plt.title("Noise classes, first 100")
    for aa in range(len(emu[-1])):
        plt.plot(emu[-1][aa][:100],color="gray",alpha=0.3)
        plt.plot(algo[-2][aa][:100],color="blue",alpha=0.3)
        plt.plot(noise_classes_sim[aa][:100],color="red",alpha=0.1)
    plt.ylim(-2,100)
    plt.axhline(config["start"],color="black")
    plt.axhline(config["stop"],color="black")
    plt.xlabel("Emulator step")
    plt.ylabel("Predicted noise level")

    plt.subplot(1,3,2)
    plt.title("Noise classes, full run")
    for aa in range(len(emu[-1])):
        plt.plot(emu[-1][aa],color="gray",alpha=0.3)
        plt.plot(algo[-2][aa],color="blue",alpha=0.3)
        plt.plot(noise_classes_sim[aa],color="red",alpha=0.1)
    plt.ylim(-2,100)
    plt.axhline(config["start"],color="black")
    plt.axhline(config["stop"],color="black")
    plt.xlabel("Emulator step")

    plt.subplot(1,3,3)
    plt.title("Noise classes, last 100")
    for aa in range(len(emu[-1])):
        plt.plot(emu[-1][aa][-100:],color="gray",alpha=0.3)
        plt.plot(algo[-2][aa][-100:],color="blue",alpha=0.3)
        plt.plot(noise_classes_sim[aa][-100:],color="red",alpha=0.1)
    plt.ylim(-2,100)
    plt.axhline(config["start"],color="black")
    plt.axhline(config["stop"],color="black")
    plt.xlabel("Emulator step")
    wandb.log({"Noise classes": wandb.Image(fig_noise_classes)})
    plt.close()

    ## Save tensors
    for aa,em in enumerate(emu):
        torch.save(em,config["save_string"]+"/emu_%d.pt" % (aa+1))
    for aa,al in enumerate(algo):
        torch.save(al,config["save_string"]+"/therm_%d.pt" % (aa+1))

    ss_emu,nan_emu=util.spectral_similarity(ke_ic[1],ke_emu[1])
    ss_therm,nan_therm=util.spectral_similarity(ke_ic[1],ke_therm[1])

    wandb.run.summary["algo time (seconds)"]=algo_time
    wandb.run.summary["spectral similarity emulator"]=ss_emu
    wandb.run.summary["nans emulator"]=nan_emu
    wandb.run.summary["spectral similarity thermalized"]=ss_therm
    wandb.run.summary["nans thermalized"]=nan_therm

    print("finished this run")