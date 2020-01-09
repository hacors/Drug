import csv
import random

from rdkit import Chem  # conda install -c conda-forge rdkit

# conda install -c rmg descriptastorus
from descriptastorus.descriptors import rdNormalizedDescriptors


class MoleculeDatapoint():
    def __init__(self, line, feature_generator):
        self.smiles = line[0]
        self.mol = Chem.MolFromSmiles(self.smiles)
        self.features = feature_generator.process(self.smiles)[1:]
        self.targets = line[1:]


def get_data(path):
    with open(path) as file:
        reader = csv.reader(file)
        next(reader)
        result = list(reader)
    return result


def preprocess(data):
    feature_generator = rdNormalizedDescriptors.RDKit2DNormalized()
    result = [[MoleculeDatapoint(line, feature_generator)] for line in data]
    return result


def split(data):
    random.shuffle(data)
    cut_1, cut_2 = int(0.8 * len(data)), int(0.9 * len(data))
    train = data[:cut_1]
    val = data[cut_1:cut_2]
    test = data[cut_2:]
    return train, val, test


def main(path):
    data = get_data(path)
    preprocess_data = preprocess(data)
    train, val, test = split(preprocess_data)
    return train, val, test


if __name__ == '__main__':
    data = get_data('Data/bbbp_test.csv')
    preprocess_data = preprocess(data)
    train, val, test = split(data)
    pass
