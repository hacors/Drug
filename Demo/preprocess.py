import csv
from rdkit import Chem  # conda install -c conda-forge rdkit
# conda install -c rmg descriptastorus
from descriptastorus.descriptors import rdNormalizedDescriptors


class MoleculeDatapoint():
    def __init__(self, line):
        pass


def get_data(path):
    with open(path) as file:
        reader = csv.reader(file)
        next(reader)
        result = list(reader)
    return result


def preprocess(data):
    feature_generator = rdNormalizedDescriptors.RDKit2DNormalized()
    result = list()
    for line in data:
        smiles = line[0]
        mol = Chem.MolFromSmiles(smiles)
        features = feature_generator.process(smiles)[1:]
        targets = line[1:]


if __name__ == '__main__':
    data = get_data('Data/bbbp_test.csv')
    preprocess(data)
