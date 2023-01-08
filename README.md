
# Dynamic defense against Adversarial Attacks 
Adversarial Machine Learning (AML) is a discipline that seeks to develop adversarial
samples in order to deceive machine learning models such as malware detectors, causing
genuine malware to be misclassified and forecasted as a benign sample. This is accomplished
by including carefully generated noise in the sample, which is sufficient to fool malware detection.
In order to counter such evasion attacks, we propose a defensive framework  which adapts during test time.

## Folder structure

```
├── DNN / TENT 
│   ├── output (generated on execution)
│   │   ├── metrics
│   |   │   ├── derived_results/*.json
│   |   │   ├── normal_results/*.json
│   │   ├── trained_models/*.pt(h)
│   │   ├── tables
│   ├── ...
│   ├── create_tables.py
│   ├── framework.py
│   ├── run_experiments.py
│   ├── parameters.ini
├── helper_files
│   ├── sample_dataset
│   │   ├── mal/*.pt
│   │   ├── ben/*.pt
│   ├── ...
│   ├── linux_environment.yml
│   ├── osx_environment.yml
├── README.md
```

---

## Requirements and Installation

- pytorch (1.10.2)
- numpy (1.19.2)
- pandas (1.1.5)
- pybloomfiltermmap (0.3.15)
- losswise (4.0)
- sklearn (0.24.2)
- matplotlib (3.3.4)
- lief (0.12.1)

The code is built using **Python 3.9.1**

All the required packages are specified in the yml files under `helper_files` folder in the root directory. If `conda` is installed, `cd` to the root directory and execute the following with `linux_environment.yml` on Linux.

_Linux_

```
conda env create --file ./helper_files/linux_environment.yml
```

This will create an environment called `damroc`.

To activate this environment, execute the following command:

```
conda activate damroc
```

## Example Usage

### Train / Test natural model

1. Configure the experiment as desired by modifying the `parameters.ini` file present inside a learner. Some of the parameters to modify are:

   - dataset filepath
   - gpu device if any
   - name of the experiment
   - training method (inner maximizer)
   - evasion method
   - other hyperparameters

   After configuring the dataset filepath and other hyperparams, set the training method to `natural`. To test the natural model with different attacks or no attack, set the evasion methon parameter to one of the following - `natural`, `dfgsm_k` (aka dBIMk), `rfgsm_k` (aka rBIMk), `topkr`(aka GRAMS) or `grosse` (aka JSMA).

   **Note** A dataset with 100 malware and 100 benign samples is provided for testing purposes. This is present in the `helper_files` folder in the root directory.

2. Execute `framework.py`

   ```
   python framework.py
   ```
---
### Train / Test using DAM-ROC defense

1. Change the current directory to dnn. Once the directory is changed, make necessary changes in the parameters.ini file (training method, evasion method).

   When an attack is specified, the code uses the DAM-ROC selector by default and trains the model with the given dataset and hyperparameters.

   After successful running of framework.py, the test results and trained models are stored in output folder of dnn directory.

   **Note:** For dynamic defense, TENT defense needs pretrained models, so we get those pretrained models from dnn trained models. For training a model, the variables 'train_model_from_scratch' and 'load_model_weights' in parameters.ini should be set True and False respectively. 

2. The trained models should then be moved to TENT -> output -> trained_models.

    After moving the pretrained models, change the current directory to tent. 

    For testing the performance of the model, the variables 'train_model_from_scratch' and 'load_model_weights' in parameters.ini should be set False and True respectively.

   After making necessary changes in parameters.ini, run framework_tent.py to get the testing results for TENT defense. 
---

## Reproduce results

In order to reproduce the results in the paper, set the parameters accordingly and execute `run_experiments.py` script. This script runs the framework for all combinations of training and evasion and stores the results.
```
python run_experiments.py
```
Results (metrics and retrained models) will be populated under 'output' directory inside the chosen learner directory (dnn/tent).