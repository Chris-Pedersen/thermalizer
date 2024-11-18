import torch
import torch_qg.model as torch_model


def normalize_qg(pv_batch,upper_std=8.6294e-06,lower_std=1.1706e-06):
    """ For a batch of normalised QG fields, return PV in physical units.
        This operation is NOT performed inplace """
    normed_batch=torch.zeros_like(pv_batch,device=pv_batch.device)
    normed_batch[:,0,:,:]=pv_batch[:,0,:,:]/upper_std
    normed_batch[:,1,:,:]=pv_batch[:,1,:,:]/lower_std

    return normed_batch

def denormalize_qg(pv_batch,upper_std=8.6294e-06,lower_std=1.1706e-06):
    """ For a batch of QG fields in physical units, return normalized PV.
        This operation is NOT performed inplace """
    denormed_batch=torch.zeros_like(pv_batch,device=pv_batch.device)
    denormed_batch[:,0,:,:]=pv_batch[:,0,:,:]*upper_std
    denormed_batch[:,1,:,:]=pv_batch[:,1,:,:]*lower_std
    
    return denormed_batch
    
def get_ke_qg(pv,ave=True,qg_model=None):
    """ Get KE spectra for a QG PV field (in physical units
    pv:    2 layer potential vorticity tensor. Can be torch tensor
           or numpy array
    ave:   bool determining whether or not we return KE spectra for upper
           and lower layers, or the depth-weighted average
    model: torch_qd model whose methods we use to evaluate KE spectra.
           can pass an instance if one is already created
    """
    if qg_model is None:
        qg_model=torch_model.PseudoSpectralModel(nx=64,dt=3600,dealias=True)
    qg_model.set_q1q2(pv)
    kespec=qg_model.get_KE_ispec()

    
    if ave:
        return qg_model.k1d_plot,kespec[0]*qg_model.delta+kespec[1]*(1-qg_model.delta)
    else:
        return qg_model.k1d_plot,kespec

def get_ke_batch(pv_batch,normed=True,qg_model=None):
    """ For a batch of QG fields, get a batch of the averaged KE
        pv_batch:   batch of qg fields with shape [batch_idx, level, nx, ny]
        normed:     are these in normalised or physical units?
        qg_model:   Can pass a torch_qg model used to calculate the KE
                    to save a bit of time
                    
                    
        returns a tuple of (k1d_plot, ke_batch)
        where k1d_plot is the wavenumber bins and ke_batch
        is a tensor of [batch_idx, len(k1d_plot)] with the KE spectra
        in each wavenumber bin
        
        """
    
    if normed:
        pv_batch=util.denormalize_qg(pv_batch)
        
    if qg_model is None:
        qg_model=torch_model.PseudoSpectralModel(nx=64,dt=3600,dealias=True,)
    ke_batch=np.zeros((len(pv_batch),23))
    
    for aa in range(len(pv_batch)):
        _,ke=util.get_ke_qg(pv_batch[aa],qg_model=qg_model)
        ke_batch[aa]=ke
    return qg_model.k1d_plot,ke_batch
    