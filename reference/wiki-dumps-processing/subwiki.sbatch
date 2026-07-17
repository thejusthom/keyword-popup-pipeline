#!/bin/bash
#SBATCH --job-name=subwiki
#SBATCH --partition=short
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=16G
#SBATCH --time=08:00:00
#SBATCH --output=/scratch/thomson.th/medhavi/logs/subwiki_%j.out
#SBATCH --error=/scratch/thomson.th/medhavi/logs/subwiki_%j.err
#SBATCH --mail-type=BEGIN,END,FAIL
#SBATCH --mail-user=thomson.th@northeastern.edu

cd /scratch/thomson.th/medhavi
module load python/3.13.5

python subwiki.py
