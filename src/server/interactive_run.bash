#!/bin/bash 
python3 ~/shape_completion/src/core/main.py

# Run on Server:Gaon3 + Queue:GIP for Time:3600 with one single gpu 
# srun -w gaon3 -p gip --time=3600 --gres=gpu:1 --job-name="ShapeCompletion" --pty ~/shape_completion/src/core/interactive_run.bash