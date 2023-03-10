# coding=utf-8
from os import system
from utils.script_functions import set_parameter

if __name__ == "__main__":
    parameters_filepath = "parameters.ini"              

    train_methods = ['natural','dfgsm_k','rfgsm_k','topkr','grosse']
    evasion_methods = ['natural','dfgsm_k', 'rfgsm_k','topkr','grosse']
        

    for train_method in train_methods:
        model_filepath = "./output/trained_models/[training_{train_meth}_evasion_{train_meth}]_run_experiments-model.pt".format(
            train_meth=train_method)

        set_parameter(parameters_filepath, "general", "training_method", train_method)
        set_parameter(parameters_filepath, "general", "train_model_from_scratch", "False")
        set_parameter(parameters_filepath, "general", "load_model_weights", "True")
        set_parameter(parameters_filepath, "general", "model_weights_path", model_filepath)

        for evasion_method in evasion_methods:
            set_parameter(parameters_filepath, "general", "evasion_method", evasion_method)
            system("python framework_tent.py")
        
            
    set_parameter(parameters_filepath, "general", "train_model_from_scratch", "True")
    set_parameter(parameters_filepath, "general", "load_model_weights", "False")
    set_parameter(parameters_filepath, "general", "experiment_suffix", "run_experiments")
    set_parameter(parameters_filepath, "general", "training_method", "natural")
    set_parameter(parameters_filepath, "general", "evasion_method", "natural")
