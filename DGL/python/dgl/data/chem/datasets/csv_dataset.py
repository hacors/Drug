from __future__ import absolute_import

import numpy as np
import os
import sys

from ...utils import save_graphs, load_graphs
from .... import backend as F

class MoleculeCSVDataset(object):
    """MoleculeCSVDataset

    This is a general class for loading molecular data from csv or pd.DataFrame.

    In data pre-processing, we set non-existing labels to be 0,
    and returning mask with 1 where label exists.

    All molecules are converted into DGLGraphs. After the first-time construction, the
    DGLGraphs will be saved for reloading so that we do not need to reconstruct them every time.

    Parameters
    ----------
    df: pandas.DataFrame
        Dataframe including smiles and labels. Can be loaded by pandas.read_csv(file_path).
        One column includes smiles and other columns for labels.
        Column names other than smiles column would be considered as task names.
    smiles_to_graph: callable, str -> DGLGraph
        A function turning a SMILES into a DGLGraph.
    node_featurizer : callable, rdkit.Chem.rdchem.Mol -> dict
        Featurization for nodes like atoms in a molecule, which can be used to update
        ndata for a DGLGraph.
    edge_featurizer : callable, rdkit.Chem.rdchem.Mol -> dict
        Featurization for edges like bonds in a molecule, which can be used to update
        edata for a DGLGraph.
    smiles_column: str
        Column name that including smiles.
    cache_file_path: str
        Path to store the preprocessed data.
    """
    def __init__(self, df, smiles_to_graph, node_featurizer, edge_featurizer,
                 smiles_column, cache_file_path):
        if 'rdkit' not in sys.modules:
            from ....base import dgl_warning
            dgl_warning(
                "Please install RDKit (Recommended Version is 2018.09.3)")
        self.df = df
        self.smiles = self.df[smiles_column].tolist()
        self.task_names = self.df.columns.drop([smiles_column]).tolist()
        self.n_tasks = len(self.task_names)
        self.cache_file_path = cache_file_path
        self._pre_process(smiles_to_graph, node_featurizer, edge_featurizer)

    def _pre_process(self, smiles_to_graph, node_featurizer, edge_featurizer):
        """Pre-process the dataset

        * Convert molecules from smiles format into DGLGraphs
          and featurize their atoms
        * Set missing labels to be 0 and use a binary masking
          matrix to mask them

        Parameters
        ----------
        smiles_to_graph : callable, SMILES -> DGLGraph
            Function for converting a SMILES (str) into a DGLGraph.
        node_featurizer : callable, rdkit.Chem.rdchem.Mol -> dict
            Featurization for nodes like atoms in a molecule, which can be used to update
            ndata for a DGLGraph.
        edge_featurizer : callable, rdkit.Chem.rdchem.Mol -> dict
            Featurization for edges like bonds in a molecule, which can be used to update
            edata for a DGLGraph.
        """
        if os.path.exists(self.cache_file_path):
            # DGLGraphs have been constructed before, reload them
            print('Loading previously saved dgl graphs...')
            self.graphs, label_dict = load_graphs(self.cache_file_path)
            self.labels = label_dict['labels']
            self.mask = label_dict['mask']
        else:
            print('Processing dgl graphs from scratch...')
            self.graphs = []
            for i, s in enumerate(self.smiles):
                print('Processing molecule {:d}/{:d}'.format(i+1, len(self)))
                self.graphs.append(smiles_to_graph(s, node_featurizer=node_featurizer,
                                                   edge_featurizer=edge_featurizer))
            _label_values = self.df[self.task_names].values
            # np.nan_to_num will also turn inf into a very large number
            self.labels = F.zerocopy_from_numpy(np.nan_to_num(_label_values).astype(np.float32))
            self.mask = F.zerocopy_from_numpy((~np.isnan(_label_values)).astype(np.float32))
            save_graphs(self.cache_file_path, self.graphs,
                        labels={'labels': self.labels, 'mask': self.mask})

    def __getitem__(self, item):
        """Get datapoint with index

        Parameters
        ----------
        item : int
            Datapoint index

        Returns
        -------
        str
            SMILES for the ith datapoint
        DGLGraph
            DGLGraph for the ith datapoint
        Tensor of dtype float32
            Labels of the datapoint for all tasks
        Tensor of dtype float32
            Binary masks indicating the existence of labels for all tasks
        """
        return self.smiles[item], self.graphs[item], self.labels[item], self.mask[item]

    def __len__(self):
        """Length of the dataset

        Returns
        -------
        int
            Length of Dataset
        """
        return len(self.smiles)
