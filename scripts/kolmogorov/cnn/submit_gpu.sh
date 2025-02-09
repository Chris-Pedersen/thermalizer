#!/bin/bash

#SBATCH --job-name=train_drn
#SBATCH --time=22:00:00
#SBATCH --cpus-per-task=10
#SBATCH --gres=gpu:1
#SBATCH --mem=64GB
#SBATCH --output=slurm_%j.out


# Begin execution
module purge

singularity exec --nv \
	    --overlay /scratch/cp3759/sing/overlay-50G-10M.ext3:ro \
	    /scratch/work/public/singularity/cuda11.1-cudnn8-devel-ubuntu18.04.sif \
	    /bin/bash -c "source /ext3/env.sh; python3 /home/cp3759/Projects/thermalizer/scripts/kolmogorov/cnn/train_cnn.py"

