# coding=utf-8
"""Python module for handling datasets for training and testing"""
import torch


import os
import pickle
import lief
import traceback
import time
import re
from random import shuffle
import glob

from torch.utils.data import Dataset, DataLoader
from sklearn.model_selection import train_test_split


MALICIOUS_LABEL = 1
BENIGN_LABEL = 0


def filter_imported_functions(func_string_with_library):
    """
    Filters the returned imported functions of binary to remove those with special characters (lots of noise for some reason),
    and require functions to start with a capital letter since Windows API functions seem to obey Upper Camelcase convension.
    """
    func_string = func_string_with_library.split(":")[1]
    
    if re.match("^[A-Z]{1}[a-zA-Z]*$", func_string):
        return True
    else:
        return False

def remove_encoding_indicator(func_string):
    """
    In many functions there is a following "A" or "W" to indicate unicode or ANSI respectively that we want to remove.
    Make a check that we have a lower case letter
    """
    if (func_string[-1] == 'A' or func_string[-1] == 'W') and func_string[-2].islower():
        return func_string[:-1]
    else:
        return func_string


def process_imported_functions_output(imports):

    imports = list(filter(lambda x: filter_imported_functions(x), imports))
    imports = list(map(lambda x: remove_encoding_indicator(x), imports))

    return imports


def create_import_to_index_mapping(parameters):
    """
     Creates mapping of all the lib imports within benign and malicious samples into their corresponding indices in the
     feature vector. The mapping is pickled to a file.
     While we can do this for each dataset. Calling this function ahead gets us the dimensionality of the feature vector.
    :param parameters: a json-like structure of the system parameters and configurations
    :return:
    """
    if eval(parameters['dataset']['load_mapping_from_pickle']):
        return

    print("Creating import to index mapping")

    malicious_filepath = parameters['dataset']['malicious_filepath']
    benign_filepath = parameters['dataset']['benign_filepath']

    imported_function_to_index = {}
    index = 0

    # List of parseable files
    if parameters['dataset']['malicious_files_list'] == 'None':
        malicious_files = os.listdir(malicious_filepath)
    else:
        malicious_files = pickle.load(
            open(get_helper_filepath(parameters, "malicious_files_list"), 'rb'))

    if parameters['dataset']['benign_files_list'] == 'None':
        benign_files = os.listdir(benign_filepath)
    else:
        benign_files = pickle.load(open(get_helper_filepath(parameters, "benign_files_list"), 'rb'))

    # Add the filepath for both malicious and benign files
    malicious_files = [os.path.join(malicious_filepath, hash_str) for hash_str in malicious_files]
    benign_files = [os.path.join(benign_filepath, hash_str) for hash_str in benign_files]

    print("Malicious files:", len(malicious_files))
    print("Benign files:", len(benign_files))
    print("Total number of files:", len(malicious_files + benign_files))
    print("Output Mapping", get_helper_filepath(parameters, "pickle_mapping_file"))

    previous_time = time.time()

    for i, hash_filepath in enumerate(malicious_files + benign_files):
        print("File Num: {num}, Length of Mapping: {length}".format(num=i, length=len(imported_function_to_index)))
        try:
            binary = lief.parse(hash_filepath)

            # imports includes the library (DLL) the function comes from
            imports = [
                lib.name.lower() + ':' + e.name for lib in binary.imports for e in lib.entries
            ]
            # preprocess imports to remove noise
            imports = process_imported_functions_output(imports)

            for lib_import in imports:

                if lib_import not in imported_function_to_index:
                    imported_function_to_index[lib_import] = index
                    index += 1

        except:
            traceback.print_exc()
            pass

    pickle.dump(imported_function_to_index,
                open(get_helper_filepath(parameters, "pickle_mapping_file"), 'wb'))


class PortableExecutableDataset(Dataset):
    def __init__(self, file_abs_locations, is_malicious, parameters):
        '''
        file_abs_locations: PE file names including path to
        is_malicious: either benign or malicious files in a single dataset
        '''
        self.file_abs_locations = file_abs_locations
        self.is_malicious = is_malicious
        self._num_features = eval(parameters["dataset"]["num_features_to_use"])
        self._is_synthetic = eval(parameters["general"]["is_synthetic_dataset"])
        if self._is_synthetic:
            self.synthetic_vector_size = int(parameters['general']['synthetic_vector_size'])
        self.use_pickle = eval(parameters["dataset"]["use_saved_feature_vectors"])
        # returns the filepath, necessary when generating pickle vector files
        self.return_filepath = eval(parameters['dataset']['generate_feature_vector_files'])

        if self._is_synthetic or self.use_pickle:
            pass
        else:
            # Need to create an index mapping or load a preloaded one
            try:
                self.imported_function_to_index = pickle.load(
                    open(get_helper_filepath(parameters, "pickle_mapping_file"), "rb"))
            except:
                imported_function_to_index = {}
                index = 0

                previous_time = time.time()
                for i, filepath in enumerate(file_abs_locations):

                    if i % 1000 == 0:
                        current_time = time.time()
                        print("Time for last 1000:", current_time - previous_time)
                        previous_time = time.time()

                    try:
                        imports_with_library = self.__get_imports_with_library(filepath)

                        for lib_import in imports_with_library:

                            if lib_import not in imported_function_to_index:
                                imported_function_to_index[lib_import] = index
                                index += 1

                    except:
                        print(i, "FAILED")
                        pass

                self.imported_function_to_index = imported_function_to_index

    def __get_imports_with_library(self, filepath):
        '''
            Helper function to get the list of imported function calls for a binary
        :param filepath: binary's absolute filepath
        :return: the list of functions called/imported appended by their libraries.
        '''
        binary = lief.parse(filepath)
        imports = [lib.name.lower() + ':' + e.name for lib in binary.imports for e in lib.entries]
        imports = process_imported_functions_output(imports)
        
        return imports

    def __len__(self):
        return len(self.file_abs_locations)

    def __getitem__(self, idx):

        if self.is_malicious:
            label = MALICIOUS_LABEL
        else:
            label = BENIGN_LABEL

        if self._is_synthetic:
            threshold = 0.2 if self.is_malicious else 0.8
            feature_vector = (torch.rand(self.synthetic_vector_size) < threshold).float()
        else:
            filepath = self.file_abs_locations[idx]

            if self.use_pickle:
                feature_vector = pickle.load(open(filepath, 'rb'))
                feature_vector = feature_vector.squeeze()

            else:
                feature_vector = [0] * len(self.imported_function_to_index)

                try:
                    # Vector of 0's initially, switch to 1's at each location corresponding to an imported function
                    imports_with_library = self.__get_imports_with_library(filepath)

                    for lib_import in imports_with_library:
                        index = self.imported_function_to_index[lib_import]

                        feature_vector[index] = 1

                except:
                    raise Exception("%s is not parseable!" % filepath)

                feature_vector = torch.Tensor(feature_vector[:self._num_features])

        if self.return_filepath:
            return feature_vector, label, filepath
        else:
            return feature_vector, label


def get_helper_filepath(parameters, filename):
    """
     Return the absolute file of the 'filename' helper file
    :param filename: file name
    :return:
    """
    filename = os.path.join(parameters["dataset"]["helper_filepath"],
                            parameters["dataset"][filename])
    print("-- accessing file:", filename)
    return filename


def load_data(parameters):
    """
        Load the training/test datasets
    :param parameters:
    :return: dictionaries of train and test dataloaders
    """

    print("Starting data loading")
    if eval(parameters["general"]["is_synthetic_dataset"]):
        num_files = int(parameters['dataset']['num_files_to_use'])
        malicious_files_abs_locs = ["1"] * num_files
        benign_files_abs_locs = ["2"] * num_files
    else:
        # generate the global index mapping file
        create_import_to_index_mapping(parameters)

        # get absolute filenames for path malicious and benign files
        malicious_filepath = parameters['dataset']['malicious_filepath']
        benign_filepath = parameters['dataset']['benign_filepath']

        if parameters['dataset']['malicious_files_list'] == 'None':
            malicious_files = os.listdir(malicious_filepath)
        else:
            malicious_files_list_file = get_helper_filepath(parameters, "malicious_files_list")
            print("Getting malicious files from ", malicious_files_list_file)
            malicious_files = pickle.load(open(malicious_files_list_file, 'rb'))

        if parameters['dataset']['benign_files_list'] == 'None':
            benign_files = os.listdir(benign_filepath)
        else:
            benign_files_list_file = get_helper_filepath(parameters, "benign_files_list")
            print("Getting benign files from ", benign_files_list_file)
            benign_files = pickle.load(open(benign_files_list_file, 'rb'))

        malicious_files_abs_locs = [os.path.join(malicious_filepath, hash) for hash in malicious_files]
        benign_files_abs_locs = [os.path.join(benign_filepath, hash) for hash in benign_files]

        # set the datasets
        if eval(parameters['dataset']['use_subset_of_data']):
            num_files = int(parameters['dataset']['num_files_to_use'])
            malicious_files_abs_locs = malicious_files_abs_locs[:num_files]
            benign_files_abs_locs = benign_files_abs_locs[:num_files]

    print("Malware Files:", len(malicious_files_abs_locs))
    print("Benign Files:", len(benign_files_abs_locs))

    # our malicious and benign datasets have the same size and have the same batch size
    # assert len(malicious_files_abs_locs) == len(
    #    benign_files_abs_locs), "It is assumed that malicious and benign dataset are of the same size"

    training_batch_size = int(parameters["hyperparam"]["training_batch_size"])
    test_batch_size = int(parameters["hyperparam"]["test_batch_size"])
    test_size_percent = float(parameters["dataset"]["test_size_percent"])

    train_malicious_files_abs_locs, test_malicious_files_abs_locs = train_test_split(
        malicious_files_abs_locs, test_size=test_size_percent)
    train_malicious_files_abs_locs, valid_malicious_files_abs_locs = train_test_split(
        train_malicious_files_abs_locs, test_size=0.25)

    train_benign_files_abs_locs, test_benign_files_abs_locs = train_test_split(
        benign_files_abs_locs, test_size=test_size_percent)
    train_benign_files_abs_locs, valid_benign_files_abs_locs = train_test_split(
        train_benign_files_abs_locs, test_size=0.25)

    print("Preparing training datasets")
    train_malicious_dataset = PortableExecutableDataset(
        train_malicious_files_abs_locs, is_malicious=True, parameters=parameters)
    train_benign_dataset = PortableExecutableDataset(
        train_benign_files_abs_locs, is_malicious=False, parameters=parameters)

    print("Preparing validation datasets")
    valid_malicious_dataset = PortableExecutableDataset(
        valid_malicious_files_abs_locs, is_malicious=True, parameters=parameters)
    valid_benign_dataset = PortableExecutableDataset(
        valid_benign_files_abs_locs, is_malicious=False, parameters=parameters)

    print("Preparing testing datasets")
    test_malicious_dataset = PortableExecutableDataset(
        test_malicious_files_abs_locs, is_malicious=True, parameters=parameters)
    test_benign_dataset = PortableExecutableDataset(
        test_benign_files_abs_locs, is_malicious=False, parameters=parameters)

    # assertion
    assert train_malicious_dataset[0][0].size() == train_benign_dataset[0][
        0].size(), "malicious and benign are of the same feature space"
    assert test_malicious_dataset[0][0].size() == test_benign_dataset[0][
        0].size(), "malicious and benign are of the same feature space"
    assert test_malicious_dataset[0][0].size() == train_benign_dataset[0][
        0].size(), "malicious and benign are of the same feature space"
    assert train_malicious_dataset[0][0].size() == test_benign_dataset[0][
        0].size(), "malicious and benign are of the same feature space"
    assert valid_malicious_dataset[0][0].size() == valid_benign_dataset[0][
        0].size(), "malicious and benign are of the same feature space"
    assert valid_malicious_dataset[0][0].size() == train_benign_dataset[0][
        0].size(), "malicious and benign are of the same feature space"

    num_features = train_benign_dataset[0][0].size()[0]

    # set the dataloaders
    num_workers = int(parameters['general']['num_workers'])

    malicious_trainloader = DataLoader(
        train_malicious_dataset, batch_size=training_batch_size, shuffle=True, num_workers=num_workers)
    benign_trainloader = DataLoader(
        train_benign_dataset, batch_size=training_batch_size, shuffle=True, num_workers=num_workers)

    malicious_validloader = DataLoader(
        valid_malicious_dataset, batch_size=training_batch_size, shuffle=True, num_workers=num_workers)
    benign_validloader = DataLoader(
        valid_benign_dataset, batch_size=training_batch_size, shuffle=True, num_workers=num_workers)

    malicious_testloader = DataLoader(
        test_malicious_dataset, batch_size=test_batch_size, shuffle=False, num_workers=num_workers)
    benign_testloader = DataLoader(
        test_benign_dataset, batch_size=test_batch_size, shuffle=False, num_workers=num_workers)

    train_dataloaders = {"malicious": malicious_trainloader, "benign": benign_trainloader}
    valid_dataloaders = {"malicious": malicious_validloader, "benign": benign_validloader}
    test_dataloaders = {"malicious": malicious_testloader, "benign": benign_testloader}

    # return the dataloaders in a dictionary
    return train_dataloaders, valid_dataloaders, test_dataloaders, num_features


def load_malicious_data(parameters):
    # Returns a dataloader for malicious data

    # With synthetic dataset, just create dummy absolute locs
    if eval(parameters['general']['is_synthetic_dataset']):
        num_files_to_use = int(parameters['dataset']['num_files_to_use'])
        malicious_files_abs_locs = ["1"] * num_files_to_use
    else:
        create_import_to_index_mapping(parameters)
        malicious_filepath = parameters['dataset']['malicious_filepath']

        num_files_to_use = int(parameters['dataset']['num_files_to_use'])

        if parameters['dataset']['malicious_files_list'] == 'None':
            malicious_files = os.listdir(malicious_filepath)
        else:
            malicious_files_list_file = get_helper_filepath(parameters, "malicious_files_list")
            print("Getting malicious files from ", malicious_files_list_file)
            malicious_files = pickle.load(open(malicious_files_list_file, 'rb'))

        malicious_files_abs_locs = [os.path.join(malicious_filepath, hash) for hash in malicious_files]
        shuffle(malicious_files_abs_locs)
        malicious_files_abs_locs = malicious_files_abs_locs[:num_files_to_use]


    print("Malware Files:", len(malicious_files_abs_locs))
    full_malicious_dataset = PortableExecutableDataset(malicious_files_abs_locs, is_malicious=True,
                                                       parameters=parameters)
    num_workers = int(parameters['general']['num_workers'])
    analysis_batch_size = int(parameters['hyperparam']['analysis_batch_size'])

    malicious_dataloader = DataLoader(full_malicious_dataset, batch_size=analysis_batch_size, shuffle=True,
                                      num_workers=num_workers)

    return malicious_dataloader


def load_benign_data(parameters):
    # Returns a dataloader for benign data

    # With synthetic dataset, just create dummy absolute locs
    if eval(parameters['general']['is_synthetic_dataset']):
        num_files_to_use = int(parameters['dataset']['num_files_to_use'])
        benign_files_abs_locs = ["2"] * num_files_to_use
    else:
        create_import_to_index_mapping(parameters)
        benign_filepath = parameters['dataset']['benign_filepath']

        num_files_to_use = int(parameters['dataset']['num_files_to_use'])

        if parameters['dataset']['benign_files_list'] == 'None':
            benign_files = os.listdir(benign_filepath)
        else:
            benign_files_list_file = get_helper_filepath(parameters, "benign_files_list")
            print("Getting benign files from ", benign_files_list_file)
            benign_files = pickle.load(open(benign_files_list_file, 'rb'))

        benign_files_abs_locs = [os.path.join(benign_filepath, hash) for hash in benign_files]
        shuffle(benign_files_abs_locs)
        benign_files_abs_locs = benign_files_abs_locs[:num_files_to_use]

    print("Benign Files:", len(benign_files_abs_locs))
    full_benign_dataset = PortableExecutableDataset(benign_files_abs_locs, is_malicious=False, parameters=parameters)

    num_workers = int(parameters['general']['num_workers'])
    analysis_batch_size = int(parameters['hyperparam']['analysis_batch_size'])

    benign_dataloader = DataLoader(full_benign_dataset, batch_size=analysis_batch_size, shuffle=True,
                                      num_workers=num_workers)

    return benign_dataloader


def load_adversarial_data(parameters, method=None):
    """
    Adversarial data always saved a pickle files
    """
    adv_filepath = parameters['dataset']['adversarial_filepath']
    # adv_files = os.listdir(adv_filepath)
    string_param = os.path.join(adv_filepath, "**")
    adv_files_abs_locs = glob.glob(string_param, recursive=True)

    # Remove symbolic links
    # Assumes a structure of training method directory followed by directories for each adversarial method
    methods = ['', 'rfgsm_k', 'dfgsm_k', 'bga_k', 'bca_k']
    for method in methods:
        adv_files_abs_locs.remove(os.path.join(adv_filepath, method))

    if method is not None:
        adv_files_abs_locs = list(filter(lambda x: method in x, adv_files_abs_locs))

    num_files_to_use = int(parameters['dataset']['num_files_to_use'])

    shuffle(adv_files_abs_locs)
    adv_files_abs_locs = adv_files_abs_locs[:num_files_to_use]

    print("Adversarial Files", len(adv_files_abs_locs))

    full_adv_dataset = PortableExecutableDataset(adv_files_abs_locs, is_malicious=True, parameters=parameters)
    num_workers = int(parameters['general']['num_workers'])

    analysis_batch_size = int(parameters['hyperparam']['analysis_batch_size'])

    adv_dataloader = DataLoader(full_adv_dataset, batch_size=analysis_batch_size, shuffle=True, num_workers=num_workers)

    return adv_dataloader


if __name__ == "__main__":
    print("I am a module to be imported by others, testing some functionalities here")
    from utils.utils import load_parameters
    parameters = load_parameters("../parameters.ini")
    train_data, valid_data, test_data, num_features = load_data(parameters=parameters)
    dset_1 = train_data["malicious"].dataset
    dset_2 = train_data["benign"].dataset
    print("A sample from malicious dataset has ", sum(dset_1[0][0]), " features, with label",
          dset_1[0][1])
    print("A sample from benign dataset has ", sum(dset_2[0][0]), " features, with label",
          dset_2[0][1])
    print("Feature space is of %d-dimensionality" % dset_1[10][0].size(), num_features)
