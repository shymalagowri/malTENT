# coding=utf-8
"""
Python module for performing adversarial training for malware detection
"""

import numpy as np
import os
os.environ['MKL_THREADING_LAYER'] = 'GNU'
import torch
import random
import torch.nn as nn
import torch.optim as optim
from torch.autograd import Variable
import tent
import norm

from utils.utils import load_parameters, stack_tensors, set_parameter
from datasets.datasets import load_data
from inner_maximizers.inner_maximizers import inner_maximizer
from nets.ff_classifier import build_ff_classifier
from blindspot_coverage.covering_number import CoveringNumber
import losswise
import time
import shutil
import json
import torch.nn.functional as F
import matplotlib.pyplot as plt
from config.gmsaconf import cfg

# Step 1. Load configuration
parameters = load_parameters("parameters.ini")
is_cuda = eval(parameters["general"]["is_cuda"])
if is_cuda:
    os.environ["CUDA_VISIBLE_DEVICES"] = parameters["general"]["gpu_device"]

assertion_message = "Set this flag off to train models."
assert eval(parameters['dataset']['generate_feature_vector_files']) is False, assertion_message

log_interval = int(parameters["general"]["log_interval"])
num_epochs = int(parameters["hyperparam"]["ff_num_epochs"])
is_losswise = eval(parameters["general"]["is_losswise"])
is_synthetic_dataset = eval(parameters["general"]["is_synthetic_dataset"])

training_method = parameters["general"]["training_method"]
evasion_method = parameters["general"]["evasion_method"]
experiment_suffix = parameters["general"]["experiment_suffix"]
experiment_name = "[training_%s_evasion_%s]_%s" % (training_method, evasion_method,
                                                   experiment_suffix)

print("Training Method:%s, Evasion Method:%s" % (training_method, evasion_method))

seed_val = int(parameters["general"]["seed"])

random.seed(seed_val)
torch.manual_seed(seed_val)
np.random.seed(seed_val)

if is_losswise:
    losswise_key = parameters['general']['losswise_api_key']

    if losswise_key == 'None':
        raise Exception("Must set API key in the parameters file to use losswise")

    losswise.set_api_key(losswise_key)

    session = losswise.Session(tag=experiment_name, max_iter=200)
    graph_loss = session.graph("loss", kind="min")
    graph_accuracy = session.graph("accuracy", kind="max")
    graph_coverage = session.graph("coverage", kind="max")
    graph_evasion = session.graph("evasion", kind="min")

evasion_iterations = int(parameters['hyperparam']['evasion_iterations'])

save_every_epoch = eval(parameters['general']['save_every_epoch'])

train_model_from_scratch = eval(parameters['general']['train_model_from_scratch'])
load_model_weights = eval(parameters['general']['load_model_weights'])
model_weights_path = parameters['general']['model_weights_path']

# Step 2. Load training and test data
train_dataloader_dict, valid_dataloader_dict, test_dataloader_dict, num_features = load_data(
    parameters)

# set the bscn metric
num_samples = len(train_dataloader_dict["malicious"].dataset)
bscn = CoveringNumber(num_samples, num_epochs * num_samples,
                      train_dataloader_dict["malicious"].batch_size)

if load_model_weights:
    print("Loading Model Weights From: {path}".format(path=model_weights_path))
    model = torch.load(model_weights_path)

else:
    # Step 3. Construct neural net (N) - this can be replaced with any model of interest
    model = build_ff_classifier(
        input_size=num_features,
        hidden_1_size=int(parameters["hyperparam"]["ff_h1"]),
        hidden_2_size=int(parameters["hyperparam"]["ff_h2"]),
        hidden_3_size=int(parameters["hyperparam"]["ff_h3"]))
# gpu related setups
if is_cuda:
    torch.cuda.manual_seed(int(parameters["general"]["seed"]))
    model = model.cuda()

# Step 4. Define loss function and optimizer  for training (back propagation block in Fig 2.)
loss_fct = nn.CrossEntropyLoss(reduction='none')
optimizer = optim.Adam(model.parameters(), lr=float(parameters["hyperparam"]["ff_learning_rate"]))

def get_beta(batch_idx, m, beta_type, epoch, num_epochs):
    if beta_type == "Blundell":
        beta = 2 ** (m - (batch_idx + 1)) / (2 ** m - 1)
    elif beta_type == "Soenderby":
        beta = min(epoch / (num_epochs // 4), 1)
    elif beta_type == "Standard":
        beta = 1 / m
    else:
        beta = 0
    return beta

def elbo(out, y, kl_sum, beta):
    ce_loss = F.cross_entropy(out, y)
    return ce_loss + beta * kl_sum

def select_mals(mal,adv,model,epoch,batch_idx):
    print("---")
    print(epoch,batch_idx)
    mal_res = model(mal.cuda())
    adv_res = model(adv.cuda())
    adv_select = []
    mal_select = []
    for i in range(mal_res.size(0)):
        m1 = mal_res[i][0]
        a1 = adv_res[i][0]
        print(m1,a1)
        if m1 < a1:
            adv_select.append(i)
        else:
            mal_select.append(i)
    print("selected mal,adv")
    print(mal_select,adv_select)
    print()

    # print(mal)
    # print()
    # print(adv)
    # print()
    
    final_t = stack_tensors(mal[[mal_select]],adv[[adv_select]])
    print(final_t)
    
    print()
    
    if torch.equal(adv,final_t):
        print("TRUE_ADV_MATCH")
        # ResultList.append('ADV')
    else:
        print("NOT_MATCH")
        # ResultList.append('HYBRID')
    print("---")
    return final_t

train_accu=[]
train_loss=[]
def train(epoch):
    model.train()
    total_correct = 0.
    total_loss = 0.
    total = 0.

    current_time = time.time()

    if is_synthetic_dataset:
        # since generation of synthetic data set is random, we'd like them to be the same over epochs
        torch.manual_seed(seed_val)
        random.seed(seed_val)

    for batch_idx, ((bon_x, bon_y), (mal_x, mal_y)) in enumerate(
            zip(train_dataloader_dict["benign"], train_dataloader_dict["malicious"])):
        # Check for adversarial learning
        mal_x1 = inner_maximizer(
            mal_x, mal_y, model, loss_fct, iterations=evasion_iterations, method=training_method)

        res = select_mals(mal_x,mal_x1,model,epoch,batch_idx)

        # stack input
        if is_cuda:
            x = Variable(stack_tensors(bon_x, mal_x,mal_x1).cuda())
            y = Variable(stack_tensors(bon_y, mal_y,mal_y).cuda())
        else:
            x = Variable(stack_tensors(bon_x, mal_x,mal_x1))
            y = Variable(stack_tensors(bon_y, mal_y,mal_y))

        # BASIC DNN TRAINING
        # UNCOMMENT THIS AND COMMENT BELOW TRAINING FOR DNN
        # forward pass
        y_model = model(x)

        # backward pass
        optimizer.zero_grad()
        loss = loss_fct(y_model, y).mean()
        loss.backward()
        optimizer.step()

        # BNN BAYES BY BACKPROP
        # BEAT OPTION = STANDARD, BLUNDELL, SOENDERBY
        # UNCOMMENT THIS AND COMMENT ABOVE TRAINING FOR BNN
        # training
        # optimizer.zero_grad()
        # y_model = model(x)
        # kl = loss_fct(y_model, y).mean()
        # print("BETA FROM TRAINING")
        # print(batch_idx, 
        #             (train_dataloader_dict["benign"].dataset.__len__() + train_dataloader_dict["malicious"].dataset.__len__()), 
        #             "Blundell", 
        #             epoch,
        #             num_epochs)
        # beta = get_beta(batch_idx, 
        #                 (train_dataloader_dict["benign"].dataset.__len__() + train_dataloader_dict["malicious"].dataset.__len__()), 
        #                 "Blundell", 
        #                 epoch,
        #                 num_epochs)
        # print(beta)
        # loss = elbo(y_model, y, kl, beta)
        # loss.backward()
        # optimizer.step()

        # predict pass
        _, predicted = torch.topk(y_model, k=1)
        correct = predicted.data.eq(y.data.view_as(predicted.data)).cpu().sum()

        # metrics
        total_loss += loss.data.item() * len(y)
        total_correct += correct
        total += len(y)

        # bscn.update_numerator_batch(batch_idx, mal_x)
        print("COVERING UPDATES",mal_x.size(0))
        for i in range(mal_x.size(0)):
            print("UPDATING NORMAL MALS")
            a = bscn.update(mal_x[i])
            print("UPDATINGS ADVS")
            b = bscn.update(mal_x1[i])
            print("UPDATING GOOD SPOTS")
            c = 1 #gscn.update(bon_x[i])
            print(a,b,c)

        if batch_idx % log_interval == 0:

            print("Time Taken:", time.time() - current_time)
            current_time = time.time()

            print(
                "Train Epoch ({}) | Batch ({}) | [{}/{} ({:.0f}%)]\tBatch Loss: {:.6f}\tBatch Accuracy: {:.1f}%\t BSCN: {:.12f}".
                format(epoch, batch_idx, batch_idx * len(x),
                       len(train_dataloader_dict["malicious"].dataset) +
                       len(train_dataloader_dict["benign"].dataset),
                       100. * batch_idx / len(train_dataloader_dict["benign"]), loss.data.item(),
                       100. * correct / len(y), bscn.ratio()))

    train_accu.append(100. * total_correct / total)
    train_loss.append(total_loss / total)
    if is_losswise:
        graph_accuracy.append(epoch, {
            "train_accuracy_%s" % experiment_name: 100. * total_correct / total
        })
        graph_loss.append(epoch, {"train_loss_%s" % experiment_name: total_loss / total})
        graph_coverage.append(epoch, {"train_coverage_%s" % experiment_name: bscn.ratio()})

    model_filename = "{name}_epoch_{e}".format(name=experiment_name, e=epoch)

    if save_every_epoch:
        torch.save(model, os.path.join("model_weights", model_filename))


eval_accu_mal_adv=[]
eval_loss_mal_adv=[]
eval_accu_mal=[]
eval_loss_mal=[]
eval_accu_ben=[]
eval_loss_ben=[]



def setup_source(model):
    """Set up the baseline source model without adaptation."""
    # model.eval()
    print(f"model for evaluation: %s", model)
    return model


def setup_norm(model):
    """Set up test-time normalization adaptation.
    Adapt by normalizing features with test batch statistics.
    The statistics are measured independently for each batch;
    no running average or other cross-batch estimation is used.
    """
    norm_model = norm.Norm(model)
    print(f"model for adaptation: %s", model)
    stats, stat_names = norm.collect_stats(model)
    print(f"stats for adaptation: %s", stat_names)
    return norm_model


def setup_tent(model):
    """Set up tent adaptation.
    Configure the model for training + feature modulation by batch statistics,
    collect the parameters for feature modulation by gradient optimization,
    set up the optimizer, and then tent the model.
    """
    model = tent.configure_model(model)
    params, param_names = tent.collect_params(model)
    optimizer = setup_optimizer(params)
    tent_model = tent.Tent(model, optimizer,
                           steps=cfg.OPTIM.STEPS,
                           episodic=cfg.MODEL.EPISODIC)
    print("Before updating ...........")
    print(f"model for adaptation: %s", model)
    print(f"params for adaptation: %s \n %s", params,param_names)
    print(f"optimizer for adaptation: %s", optimizer)
    return tent_model


def setup_optimizer(params):
    """Set up optimizer for tent adaptation.
    Tent needs an optimizer for test-time entropy minimization.
    In principle, tent could make use of any gradient optimizer.
    In practice, we advise choosing Adam or SGD+momentum.
    For optimization settings, we advise to use the settings from the end of
    trainig, if known, or start with a low learning rate (like 0.001) if not.
    For best results, try tuning the learning rate and batch size.
    """
    if cfg.OPTIM.METHOD == 'Adam':
        return optim.Adam(params,
                    lr=cfg.OPTIM.LR,
                    betas=(cfg.OPTIM.BETA, 0.999),
                    weight_decay=cfg.OPTIM.WD)
    elif cfg.OPTIM.METHOD == 'SGD':
        return optim.SGD(params,
                   lr=cfg.OPTIM.LR,
                   momentum=cfg.OPTIM.MOMENTUM,
                   dampening=cfg.OPTIM.DAMPENING,
                   weight_decay=cfg.OPTIM.WD,
                   nesterov=cfg.OPTIM.NESTEROV)
    else:
        raise NotImplementedError


def check_one_category(category="benign", is_validate=False, is_evade=False,
                       evade_method='dfgsm_k'):
    """
    test the model in terms of loss and accuracy on category, this function also allows to perform perturbation
    with respect to loss to evade
    :param category: benign or malicious dataset
    :param is_validate: validation or testing dataset
    :param is_evade: to perform evasion or not
    :param evade_method: evasion method (we can use on of the inner maximier methods), it is only relevant if is_evade
      is True
    :return:
    """
    model.eval()
    total_loss = 0
    total_correct = 0
    total = 0
    evasion_mode = ""

    if is_synthetic_dataset:
        # since generation of synthetic data set is random, we'd like them to be the same over epochs
        torch.manual_seed(seed_val)
        random.seed(seed_val)

    if is_validate:
        dataloader = valid_dataloader_dict[category]
    else:
        dataloader = test_dataloader_dict[category]

    for batch_idx, (x, y) in enumerate(dataloader):
        #
        if is_evade:
            x = inner_maximizer(
                x, y, model, loss_fct, iterations=evasion_iterations, method=evade_method)
            evasion_mode = "(evasion using %s)" % evade_method
        # stack input
        if is_cuda:
            x = Variable(x.cuda())
            y = Variable(y.cuda())
        else:
            x = Variable(x)
            y = Variable(y)

        # forward pass
        y_model = model(x)

        # loss pass
        loss = loss_fct(y_model, y).mean()

        # predict pass
        _, predicted = torch.topk(y_model, k=1)
        correct = predicted.data.eq(y.data.view_as(predicted.data)).cpu().sum()

        # metrics
        total_loss += loss.data.item() * len(y)
        total_correct += correct
        total += len(y)

    if is_validate and category=='malicious' and not is_evade:
        eval_accu_mal.append(100. * total_correct / total)
        eval_loss_mal.append(total_loss / total)

    if is_validate and category=='benign' and not is_evade:
        eval_accu_ben.append(100. * total_correct / total)
        eval_loss_ben.append(total_loss / total)
    
    if is_validate and category=='malicious' and is_evade:
        eval_accu_mal_adv.append(100. * total_correct / total)
        eval_loss_mal_adv.append(total_loss / total)

    print("{} set for {} {}: Average Loss: {:.4f}, Accuracy: {:.2f}%".format(
        "Valid" if is_validate else "Test", category, evasion_mode, total_loss / total,
        total_correct * 100. / total))

    return total_loss, total_correct, total

eval_accu_overall=[]
eval_loss_overall=[]
def test(epoch, is_validate=False):
    """
    Function to be used for both testing and validation
    :param epoch: current epoch
    :param is_validate: is the testing done on the validation dataset
    :return: average total loss, dictionary of the metrics for both bon and mal samples
    """
    # test for accuracy and loss
    bon_total_loss, bon_total_correct, bon_total = check_one_category(
        category="benign", is_evade=False, is_validate=is_validate)
    mal_total_loss, mal_total_correct, mal_total = check_one_category(
        category="malicious", is_evade=False, is_validate=is_validate)

    # test for evasion on malicious sample
    evade_mal_total_loss, evade_mal_total_correct, evade_mal_total = check_one_category(
        category="malicious", is_evade=True, evade_method=evasion_method, is_validate=is_validate)

    total_loss = bon_total_loss + mal_total_loss
    total_correct = bon_total_correct + mal_total_correct
    total = bon_total + mal_total

    dataset_type = "valid" if is_validate else "test"

    print("{} set overall: Average Loss: {:.4f}, Accuracy: {:.2f}%".format(
        dataset_type, total_loss / total, total_correct * 100. / total))

    if is_validate:
        eval_accu_overall.append(100. * total_correct / total)
        eval_loss_overall.append(total_loss / total)

    if is_losswise:
        graph_accuracy.append(
            epoch, {
                "%s_accuracy_%s" % (dataset_type, experiment_name): 100. * total_correct / total
            })
        graph_loss.append(epoch, {
            "%s_loss_%s" % (dataset_type, experiment_name): total_loss / total
        })
        graph_evasion.append(
            epoch, {
                "%s_evasion_%s" % (dataset_type, experiment_name):
                100 * (evade_mal_total - evade_mal_total_correct) / evade_mal_total
            })

    metrics = {
        "bscn_ratio": bscn.ratio(),
        "mal": {
            "total_loss": mal_total_loss,
            "total_correct": mal_total_correct.item(),
            "total": mal_total,
            "evasion": {
                "total_loss": evade_mal_total_loss,
                "total_correct": evade_mal_total_correct.item(),
                "total": evade_mal_total
            }
        },
        "bon": {
            "total_loss": bon_total_loss,
            "total_correct": bon_total_correct.item(),
            "total_evade": None,
            "total": bon_total
        }
    }

    # print(metrics)

    return (bon_total_loss + max(mal_total_loss, evade_mal_total_loss)) / total, metrics

def generate_loss_figures():
    cwd = os.getcwd()
    dir = 'loss_graphs'
    try:
        os.chdir(dir)
        print("Current dir is", os.getcwd())
        original_parameters_filepath = "figure_generation_parameters.ini"
        exp_time = time.strftime("%m_%d_%Hh_%Mm", time.localtime())

        new_params_directory = "experiment_parameters"
        if not os.path.exists(new_params_directory):
            os.mkdir(new_params_directory)
        new_params_name = "loss_landscape_parameters_{exp}_{time}.ini".format(
            exp=experiment_suffix, time=exp_time)

        new_params_filepath = os.path.join(new_params_directory, new_params_name)
        shutil.copy(original_parameters_filepath, new_params_filepath)
        new_params = load_parameters(new_params_filepath)

        trained_model_directory = "../helper_files"

        model_filepath = os.path.join(trained_model_directory, f"[training:{training_method}|evasion:{evasion_method}]_{experiment_suffix}-model.pt")

        evasion_methods = ['rfgsm_k', 'dfgsm_k', 'grosse', 'dpgdl1', 'rpgdl1', 'topkr']
        set_parameter(new_params_filepath, "general", "generate_histogram", "False")

        set_parameter(new_params_filepath, "general", "training_method", training_method)
        set_parameter(new_params_filepath, "general", "model_weights_path", model_filepath)

        for evasion in evasion_methods:
            set_parameter(new_params_filepath, "general", "evasion_method", evasion)
            start_time = time.time()
            # source activate nn_mal;
            os.system("python generate_loss_figures.py {params} {time}".format(
                params=new_params_filepath, time=exp_time))
            print("Time to run loss landscape train/evasion pair:", time.time() - start_time)
    except:
        print("Problem changing directory!")
    finally:
        print("Reverting back to old directory.")
        os.chdir(cwd)
        print("Current dir is", os.getcwd())

def create_tex_files():
    cwd = os.getcwd()
    dir = 'utils'
    try:
        os.chdir(dir)
        print("Current dir is", os.getcwd())
        os.system("python script_functions.py")
    except:
        print("Problem changing directory!")
    finally:
        print("Reverting back to old directory.")
        os.chdir(cwd)
        print("Current dir is", os.getcwd())

def save_loss_accu_graph():
    plt.figure(figsize=(10,5))
    plt.title("Training and Validation Loss") 
    plt.plot(train_loss,label="train")
    plt.plot(eval_loss_mal_adv,label="val_mal_adv")
    plt.plot(eval_loss_mal,label="val_mal")
    plt.plot(eval_loss_ben,label="val_ben")
    plt.plot(eval_loss_overall,label="val_overall")
    plt.xlabel("Epochs")
    plt.ylabel("Loss")
    plt.legend()
    plt.savefig(f"./output/metrics/{experiment_name}_loss.png")

    plt.figure(figsize=(10,5))
    plt.title("Training and Validation Accuracy") 
    plt.plot(train_accu,label="train")
    plt.plot(eval_accu_mal_adv,label="val_mal_adv")
    plt.plot(eval_accu_mal,label="val_mal")
    plt.plot(eval_accu_ben,label="val_ben")
    plt.plot(eval_accu_overall,label="val_overall")
    plt.xlabel("Epochs")
    plt.ylabel("Accuracy")
    plt.legend()
    plt.savefig(f"./output/metrics/{experiment_name}_accuracy.png")

def derived_metrics(metrics, derived_results_dir):
    print("Saving dervived metrics!")

    #natural metrics calculation
    nat_tp = metrics['mal']['total_correct']
    nat_fp =  metrics['bon']['total'] -  metrics['bon']['total_correct']
    nat_fn =  metrics['mal']['total'] -  metrics['mal']['total_correct']
    nat_tn =  metrics['bon']['total_correct']
    nat_TPR = nat_tp / (nat_tp + nat_fn)
    nat_FPR = nat_fp / (nat_fp + nat_tn)
    nat_F1 = (2* nat_tp)/ (2*nat_tp + nat_fp + nat_fn)
    nat_acc = (nat_tp + nat_tn)/(nat_tp + nat_tn + nat_fn + nat_fp)
    nat_pre = (nat_tp)/(nat_tp+nat_fp) 

    #evasion metrics calculation
    ev_tp =  metrics['mal']['evasion']['total_correct']
    ev_fp = metrics['bon']['total'] -  metrics['bon']['total_correct']
    ev_fn = metrics['mal']['evasion']['total'] - metrics['mal']['evasion']['total_correct']
    ev_tn = metrics['bon']['total_correct']
    ev_TPR = ev_tp / (ev_tp + ev_fn)
    ev_FPR = ev_fp / (ev_fp + ev_tn)
    ev_F1 = (2* ev_tp)/ (2*ev_tp + ev_fp + ev_fn)
    ev_acc = (ev_tp + ev_tn)/(ev_tp + ev_tn + ev_fn + ev_fp)

    rr_sensitivity = ev_TPR/nat_TPR

    #evasion rate
    ev_rate = (metrics['mal']['evasion']['total'] - metrics['mal']['evasion']['total_correct'])/metrics['mal']['evasion']['total']
    
    #misclassification rate
    misclass_rate = 1 - ev_acc

    derived_metrics = {
        "natural": {
            "Nat_TP": nat_tp,
            "Nat_FP": nat_fp, 
            "Nat_FN": nat_fn,
            "Nat_TN": nat_tn,
            "Nat_accuracy": nat_acc,
            "Nat_TPR": nat_TPR,
            "Nat_FPR": nat_FPR,
            "Nat_F1": nat_F1,
            "Nat_Precision": nat_pre
        },
        "evasion":{
            "Ev_TP": ev_tp,
            "Ev_FP": ev_fp, 
            "Ev_FN": ev_fn,
            "Ev_TN": ev_tn,
            "Ev_accuracy": ev_acc,
            "Ev_TPR": ev_TPR,
            "Ev_FPR": ev_FPR,
            "Ev_F1": ev_F1
        },
        "robustness_ratio":{
            "RR_sensitivity": rr_sensitivity
        },
        "evasion_rate":{
            "evasion_rate_fnr": ev_rate 
        },
        "misclassification_rate_ev":{
            "misclass_rate_ev": misclass_rate
        }}

    print('\n******DERIVED METRICS***********\n')
    print(derived_metrics)
    print('\n********************************\n')

    with open(os.path.join(derived_results_dir, experiment_name + ".json"), "w") as derived_result_file:
        json.dump(derived_metrics, derived_result_file)


def evaluate(model):
    # configure model


    base_model = model
    if cfg.MODEL.ADAPTATION == "source":
        print("test-time adaptation: NONE")
        model = setup_source(base_model)
    if cfg.MODEL.ADAPTATION == "norm":
        print("test-time adaptation: NORM")
        model = setup_norm(base_model)
    if cfg.MODEL.ADAPTATION == "tent":
        print("test-time adaptation: TENT")
        model = setup_tent(base_model)
    # evaluate on each severity and type of corruption in turn
    for severity in cfg.CORRUPTION.SEVERITY:
        for corruption_type in cfg.CORRUPTION.TYPE:
            # reset adaptation for each combination of corruption x severity
            # note: for evaluation protocol, but not necessarily needed
            try:
                model.reset()
                print("resetting model")
            except:
                print("not resetting model")
            
            total_loss = 0
            total_correct = 0
            total = 0

            dataloader = test_dataloader_dict["benign"]
            for batch_idx, (x, y) in enumerate(dataloader):
                #
                if(False):
                    x = inner_maximizer(
                        x, y, model, loss_fct, iterations=evasion_iterations, method="rfgsm_k")
                    evasion_mode = "(evasion using grosse)"
                # stack input
                if is_cuda:
                    x = Variable(x.cuda())
                    y = Variable(y.cuda())
                else:
                    x = Variable(x)
                    y = Variable(y)

                # forward pass
                y_model = model(x)



                # loss pass
                loss = loss_fct(y_model, y).mean()

                # predict pass
                _, predicted = torch.topk(y_model, k=1)
                correct = predicted.data.eq(y.data.view_as(predicted.data)).cuda().sum()

                # metrics
                total_loss += loss.data.item() * len(y)
                total_correct += correct
                total += len(y)

            bon_total_loss, bon_total_correct, bon_total= total_loss, total_correct, total
        
            total_loss = 0
            total_correct = 0
            total = 0
            dataloader = test_dataloader_dict["malicious"]
            for batch_idx, (x, y) in enumerate(dataloader):
                #
                if(False):
                    x = inner_maximizer(
                        x, y, model, loss_fct, iterations=evasion_iterations, method="rfgsm_k")
                    evasion_mode = "(evasion using grosse)"
                # stack input
                if is_cuda:
                    x = Variable(x.cuda())
                    y = Variable(y.cuda())
                else:
                    x = Variable(x)
                    y = Variable(y)

                # forward pass
                y_model = model(x)



                # loss pass
                loss = loss_fct(y_model, y).mean()

                # predict pass
                _, predicted = torch.topk(y_model, k=1)
                correct = predicted.data.eq(y.data.view_as(predicted.data)).cuda().sum()

                # metrics
                total_loss += loss.data.item() * len(y)
                total_correct += correct
                total += len(y)

            mal_total_loss, mal_total_correct, mal_total=total_loss, total_correct, total


            total_loss = 0
            total_correct = 0
            total = 0
            dataloader = test_dataloader_dict["malicious"]
            for batch_idx, (x, y) in enumerate(dataloader):
                #
                if(True):
                    x = inner_maximizer(x, y, model, loss_fct, iterations=evasion_iterations, method=evasion_method)
                    evasion_mode = "(evasion using %s)" % evasion_method
                # stack input
                if is_cuda:
                    x = Variable(x.cuda())
                    y = Variable(y.cuda())
                else:
                    x = Variable(x)
                    y = Variable(y)

                # forward pass
                y_model = model(x)



                # loss pass
                loss = loss_fct(y_model, y).mean()

                # predict pass
                _, predicted = torch.topk(y_model, k=1)
                correct = predicted.data.eq(y.data.view_as(predicted.data)).cuda().sum()

                # metrics
                total_loss += loss.data.item() * len(y)
                total_correct += correct
                total += len(y)

            evade_mal_total_loss, evade_mal_total_correct, evade_mal_total= total_loss, total_correct, total


    total_loss = bon_total_loss + mal_total_loss
    total_correct = bon_total_correct + mal_total_correct
    total = bon_total + mal_total

    dataset_type = "test"
    is_validate=False

    print("{} set overall: Average Loss: {:.4f}, Accuracy: {:.2f}%".format(
        dataset_type, total_loss / total, total_correct * 100. / total))

    if is_validate:
        eval_accu_overall.append(100. * total_correct / total)
        eval_loss_overall.append(total_loss / total)

    metrics = {

        "bscn_ratio": bscn.ratio(),
        "mal": {
            "total_loss": mal_total_loss,
            "total_correct": mal_total_correct.item(),
            "total": mal_total,
            "evasion": {
                "total_loss": evade_mal_total_loss,
                "total_correct": evade_mal_total_correct.item(),
                "total": evade_mal_total
            }
        },
        "bon": {
            "total_loss": bon_total_loss,
            "total_correct": bon_total_correct.item(),
            "total_evade": None,
            "total": bon_total
        }
    }

    # print(metrics)

    return model, metrics


if __name__ == "__main__":

    log_dir = "output/metrics"
    save_dir = "output/trained_models"

    # Check the save_dir exists or not
    if not os.path.exists("output"):
        os.makedirs("output")
    if not os.path.exists(save_dir):
        os.makedirs(save_dir)

    derived_results_dir = os.path.join(log_dir, 'derived_results')
    if not os.path.exists(derived_results_dir):
        os.makedirs(derived_results_dir)
    results_dir = os.path.join(log_dir, 'normal_results')
    if not os.path.exists(results_dir):
        os.makedirs(results_dir)

    _metrics = None
    session = None
    if train_model_from_scratch:
        best_valid_loss = float("inf")
        for _epoch in range(num_epochs):
            # train
            train(_epoch)

            # validate
            valid_loss, _ = test(_epoch, is_validate=True)

            # keep the best parameters w.r.t validation and check the test set
            if best_valid_loss > valid_loss:
                best_valid_loss = valid_loss
                _, metrics = test(_epoch, is_validate=False)

                bscn_to_save = bscn.ratio()
                with open(os.path.join(results_dir, "%s_bscn.txt" % experiment_name), "w") as f:
                    f.write(str(bscn_to_save))

                torch.save(model, os.path.join(save_dir, "%s-model.pt" % experiment_name))
            elif _epoch % log_interval == 0:
                test(_epoch, is_validate=False)

    #    save_loss_accu_graph()

    else:
        model = torch.load(model_weights_path)
        for i in range(1):
            model, metrics = evaluate(model)
            print("Training : "+training_method+" ,Evasion: "+evasion_method)
            print('\n******METRICS*********** for '+str(i)+" iteration \n")
            print(metrics)
            print('\n************************\n')



            #natural metrics calculation
            nat_tp = metrics['mal']['total_correct']
            nat_fp =  metrics['bon']['total'] -  metrics['bon']['total_correct']
            nat_fn =  metrics['mal']['total'] -  metrics['mal']['total_correct']
            nat_tn =  metrics['bon']['total_correct']
            nat_TPR = nat_tp / (nat_tp + nat_fn)
            nat_FPR = nat_fp / (nat_fp + nat_tn)
            nat_F1 = (2* nat_tp)/ (2*nat_tp + nat_fp + nat_fn)
            nat_acc = (nat_tp + nat_tn)/(nat_tp + nat_tn + nat_fn + nat_fp)
            nat_pre = (nat_tp)/(nat_tp+nat_fp) 

            #evasion metrics calculation
            ev_tp =  metrics['mal']['evasion']['total_correct']
            ev_fp = metrics['bon']['total'] -  metrics['bon']['total_correct']
            ev_fn = metrics['mal']['evasion']['total'] - metrics['mal']['evasion']['total_correct']
            ev_tn = metrics['bon']['total_correct']
            ev_TPR = ev_tp / (ev_tp + ev_fn)
            ev_FPR = ev_fp / (ev_fp + ev_tn)
            ev_F1 = (2* ev_tp)/ (2*ev_tp + ev_fp + ev_fn)
            ev_acc = (ev_tp + ev_tn)/(ev_tp + ev_tn + ev_fn + ev_fp)

            rr_sensitivity = ev_TPR/nat_TPR

            #evasion rate
            ev_rate = (metrics['mal']['evasion']['total'] - metrics['mal']['evasion']['total_correct'])/metrics['mal']['evasion']['total']
            
            #misclassification rate
            misclass_rate = 1 - ev_acc

            derived_metrics_1 = {
                "natural": {
                    "Nat_TP": nat_tp,
                    "Nat_FP": nat_fp, 
                    "Nat_FN": nat_fn,
                    "Nat_TN": nat_tn,
                    "Nat_accuracy": nat_acc,
                    "Nat_TPR": nat_TPR,
                    "Nat_FPR": nat_FPR,
                    "Nat_F1": nat_F1,
                    "Nat_Precision": nat_pre
                },
                "evasion":{
                    "Ev_TP": ev_tp,
                    "Ev_FP": ev_fp, 
                    "Ev_FN": ev_fn,
                    "Ev_TN": ev_tn,
                    "Ev_accuracy": ev_acc,
                    "Ev_TPR": ev_TPR,
                    "Ev_FPR": ev_FPR,
                    "Ev_F1": ev_F1
                },
                "robustness_ratio":{
                    "RR_sensitivity": rr_sensitivity
                },
                "evasion_rate":{
                    "evasion_rate_fnr": ev_rate 
                },
                "misclassification_rate_ev":{
                    "misclass_rate_ev": misclass_rate
                }}

            print('\n******DERIVED METRICS*********** for '+str(i)+" iteration \n")
            print(derived_metrics_1)
            print('\n********************************\n')
                    

    # print('\n******METRICS***********\n')
    # print(metrics)
    # print('\n************************\n')

    with open(os.path.join(results_dir, experiment_name + ".json"), "w") as result_file:
        json.dump(metrics, result_file)

    derived_metrics(metrics, derived_results_dir)


    if is_losswise:
       session.done()

    