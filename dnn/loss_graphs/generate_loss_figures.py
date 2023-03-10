"""
File for plotting loss progression and histograms
"""
import sys
import os
sys.path.insert(1, os.path.join(sys.path[0], '..'))

import torch
import torch.nn as nn

from inner_maximizers.inner_maximizers import inner_maximizer
from datasets.datasets import load_malicious_data
from utils.utils import load_parameters

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import matplotlib.lines as mlines

import random
import numpy as np
import math
import time
import itertools
import pickle

EVASION_METHODS = ['rfgsm_k', 'dfgsm_k'] #, 'grosse', 'topkr'
COLORS = ['b', 'g'] # , 'r', 'c'

legend_handles = []
# Create the color legend
for method, c in zip(EVASION_METHODS, COLORS):
    legend_handles.append(mpatches.Patch(color=c))

legend_labels = list(EVASION_METHODS)
legend_labels = [m + " evasion" for m in legend_labels]

parameters_filepath = sys.argv[1]
parameters = load_parameters(parameters_filepath)

exp_time = sys.argv[2]

is_cuda = eval(parameters["general"]["is_cuda"])
if is_cuda:
    os.environ["CUDA_VISIBLE_DEVICES"] = parameters["general"]["gpu_device"]

seed_val = int(parameters["general"]["seed"])
use_seed = eval(parameters["general"]["use_seed"])

if use_seed:
    random.seed(seed_val)
    torch.manual_seed(seed_val)
    np.random.seed(seed_val)

generate_histogram = eval(parameters['general']['generate_histogram'])

evasion_iterations = int(parameters['hyperparam']['evasion_iterations'])
evasion_method = parameters["general"]["evasion_method"]
train_method = parameters["general"]["training_method"]

epsilon = float(parameters['hyperparam']['evasion_epsilon'])
print("Evasion Method:", evasion_method)

# For each sample, we generate a certain number of other viable samples where functionality is preserved
extra_points_for_each_sample = int(parameters['hyperparam']['extra_points_for_each_sample'])
num_malware_samples_to_use = int(parameters['hyperparam']['num_malware_samples_to_use'])


model_weights_path = parameters['general']['model_weights_path']
print("Loading Model Weights From: {path}".format(path=model_weights_path))
model = torch.load(model_weights_path, map_location=torch.device('cpu'))

if is_cuda:
    if use_seed:
        torch.cuda.manual_seed(seed_val)
    model.cuda()

malicious_dataloader = load_malicious_data(parameters)

loss_fct = nn.NLLLoss(reduction='none')


def train_single_adversarial():
    """
    With a single adversarial point, use an evasion method to train it and points in its feasible ball region
    over a fixed number of iterations, keeping track of loss
    """
    print("Train Single Adversarial")

    base_figure_directory = "loss_progressions"
    if not os.path.exists(base_figure_directory):
        os.mkdir(base_figure_directory)

    # Output will be in a folder with the current time
    figure_directory = os.path.join(base_figure_directory, exp_time)
    if not os.path.exists(figure_directory):
        os.mkdir(figure_directory)

    model.eval()
    fig = plt.figure()

    for i, (mal_x, mal_y) in enumerate(malicious_dataloader):

        print(i, train_method, evasion_method)
        if i == num_malware_samples_to_use:
            break

        # Axis labels and title
        plt.xlabel("Inner Maximization Iterations", fontsize=14, fontweight='bold')
        plt.ylabel("Loss Value", fontsize=14, fontweight='bold')

        all_losses = []

        # Use the original sample - solid line
        _, losses = inner_maximizer(mal_x, mal_y, model, loss_fct, iterations=evasion_iterations,
                                    method=evasion_method, return_loss=True)
        losses[-1] = losses[-1].item()
        plt.plot(losses, linestyle='solid', linewidth=2.0)
        all_losses.append(losses)

        filename = "training_{t_method}_evasion_{e_method}_{num}_evasion_iterations_{mal_num}_sample.png".format(
            t_method=train_method, e_method=evasion_method, num=evasion_iterations, mal_num=i)

        pickle.dump(all_losses, open(os.path.join(figure_directory, filename + ".p"), "wb"))
        fig.savefig(os.path.join(figure_directory, filename))

        if matplotlib.get_backend() != 'Agg':
            plt.show()

        plt.clf()
        
def plot_histogram():
    print("Plotting All Methods Histogram")

    base_figure_directory = "histograms"
    if not os.path.exists(base_figure_directory):
        os.mkdir(base_figure_directory)

    # Output will be in a folder with the current time
    figure_directory = os.path.join(base_figure_directory, exp_time)
    if not os.path.exists(figure_directory):
        os.mkdir(figure_directory)

    model.eval()

    # Axis labels and title
    for i, (mal_x, mal_y) in enumerate(malicious_dataloader):
        if i == num_malware_samples_to_use:
            break

        fig, ax = plt.subplots()
        plt.xlabel("Loss Values")
        plt.ylabel("Counts")

        final_loss_values = []

        for eva_method, c in zip(EVASION_METHODS, COLORS):
            loss_values = []
            for j in range(extra_points_for_each_sample):
                print(j)
                _, losses = inner_maximizer(mal_x, mal_y, model, loss_fct, iterations=evasion_iterations,
                                            method=eva_method,return_loss=True)
                # print(losses)
                loss_values.append(losses[-1][0])


            final_loss_values.append(loss_values)

        max_loss = max(list(itertools.chain.from_iterable(final_loss_values)))
        num_bins = 50
        bin_width = float(max_loss/num_bins)

        plt.hist(final_loss_values, bins=np.arange(0, 1.05*max_loss, float(bin_width)),
                 color=COLORS, stacked=True)

        plt.legend(handles=legend_handles, labels=legend_labels)

        filename = "histogram_{t_method}-training_all_evasions_{mal_num}_sample_{extra}_extra_points".format(
            t_method=train_method, mal_num=i, extra=extra_points_for_each_sample)
        fig.savefig(os.path.join(figure_directory, filename))

        if matplotlib.get_backend() != 'Agg':
            plt.show()

        plt.clf()

        pickle.dump(final_loss_values, open(os.path.join(figure_directory, filename+".p"), "wb"))


if __name__ == '__main__':
    
    if generate_histogram:
        plot_histogram()
    else:
        train_single_adversarial()
