#!/bin/bash
#SBATCH --job-name=subwiki
#SBATCH --partition=short
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=16G
#SBATCH --time=08:00:00
#SBATCH --output=logs/subwiki_%j.out
#SBATCH --error=logs/subwiki_%j.err
#SBATCH --mail-type=BEGIN,END,FAIL
#SBATCH --mail-user=you@example.edu

# Edit the paths above (--output, --error, --mail-user) and the cd target
# below for your own cluster account before submitting with: sbatch subwiki.sbatch

cd /path/to/your/project/pipeline/01_wiki_extract
module load python/3.13.5

python subwiki.py
