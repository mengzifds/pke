# -*- coding: utf-8 -*-
# Author: Florian Boudin
# Date: 09-11-2018

"""Multipartite graph keyphrase extraction model.

Graph-based ranking approach to keyphrase extraction described in:

* Florian Boudin.
  Unsupervised Keyphrase Extraction with Multipartite Graphs.
  *In proceedings of NAACL*, pages 667-672, 2018.

"""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

from pke.unsupervised import TopicRank
from nltk.corpus import stopwords

import math
import string
import networkx as nx
import numpy as np

from scipy.spatial.distance import pdist
from scipy.cluster.hierarchy import linkage, fcluster

from itertools import combinations


class MultipartiteRank(TopicRank):
    """Multipartite graph keyphrase extraction model.

    Parameterized example::

        import pke
        import string
        from nltk.corpus import stopwords

        # 1. create a MultipartiteRank extractor.
        extractor = pke.unsupervised.MultipartiteRank(input_file='input.xml')

        # 2. load the content of the document.
        extractor.read_document(format='corenlp')

        # 3. select the longest sequences of nouns and adjectives, that do
        #    not contain punctuation marks or stopwords as candidates.
        pos = set(['NN', 'NNS', 'NNP', 'NNPS', 'JJ', 'JJR', 'JJS'])
        stoplist = list(string.punctuation)
        stoplist += ['-lrb-', '-rrb-', '-lcb-', '-rcb-', '-lsb-', '-rsb-']
        stoplist += stopwords.words('english')
        extractor.candidate_selection(pos=pos, stoplist=stoplist)

        # 4. build the Multipartite graph and rank candidates using random walk,
        #    alpha controls the weight adjustment mechanism, see TopicRank for
        #    threshold/method parameters.
        extractor.candidate_weighting(alpha=1.1,
                                      threshold=0.74,
                                      method='average')

        # 5. get the 10-highest scored candidates as keyphrases
        keyphrases = extractor.get_n_best(n=10)

    """

    def __init__(self, input_file=None, language='english'):
        """Redefining initializer for MultipartiteRank.

        Args:
            input_file (str): path to the input file, defaults to None.
            language (str): language of the document, used for stopwords list,
                default to 'english'.

        """

        super(MultipartiteRank, self).__init__(input_file=input_file,
                                               language=language)

        self.topic_identifiers = {}
        """ A container for linking candidates to topic identifiers. """

        self.graph = nx.DiGraph()
        """ Redefine the graph as a directed graph. """


    def topic_clustering(self,
                         threshold=0.74,
                         method='average'):
        """ Clustering candidates into topics.

            Args:
                threshold (float): the minimum similarity for clustering,
                    defaults to 0.74, i.e. more than 1/4 of stem overlap
                    similarity. 
                method (str): the linkage method, defaults to average.
        """

        # handle document with only one candidate
        if len(self.candidates) == 1:
            candidate = list(self.candidates)[0]
            self.topics.append([candidate])
            self.topic_identifiers[candidate] = 0
            return

        # vectorize the candidates
        candidates, X = self.vectorize_candidates()

        # compute the distance matrix
        Y = pdist(X, 'jaccard')
        Y =  np.nan_to_num(Y)

        # compute the clusters
        Z = linkage(Y, method=method)

        # form flat clusters
        clusters = fcluster(Z, t=threshold, criterion='distance')

        # for each cluster id
        for cluster_id in range(1, max(clusters)+1):
            self.topics.append([candidates[j] for j in range(len(clusters))
                                if clusters[j] == cluster_id])

        # assign cluster identifiers to candidates
        for i, cluster_id in enumerate(clusters):
            self.topic_identifiers[candidates[i]] = cluster_id-1


    def build_topic_graph(self):
        """ Build the Multipartite graph. """

        # adding the nodes to the graph
        self.graph.add_nodes_from(self.candidates.keys())

        # pre-compute edge weights
        for node_i, node_j in combinations(self.candidates.keys(), 2):

            # discard intra-topic edges
            if self.topic_identifiers[node_i] == self.topic_identifiers[node_j]:
                continue

            weights = []
            for p_i in self.candidates[node_i].offsets:
                for p_j in self.candidates[node_j].offsets:

                    # compute gap
                    gap = abs(p_i - p_j)

                    # alter gap according to candidate length
                    if p_i < p_j:
                        gap -= len(self.candidates[node_i].lexical_form) - 1
                    if p_j < p_i:
                        gap -= len(self.candidates[node_j].lexical_form) - 1
                    
                    weights.append(1.0 / gap)

            # add weighted edges 
            if weights:
                # node_i -> node_j
                self.graph.add_edge(node_i, node_j, weight=sum(weights))
                # node_j -> node_i
                self.graph.add_edge(node_j, node_i, weight=sum(weights))


    def weight_adjustment(self, alpha=1.1):
        """ Adjust edge weights for boosting some candidates.

            Args:
                alpha (float): hyper-parameter that controls the strength of the
                    weight adjustment, defaults to 1.1.
        """

        # weighted_edges = defaultdict(list)
        weighted_edges = {}

        # find the sum of all first positions
        norm = sum([s.length for s in self.sentences])

        # Topical boosting
        for variants in self.topics:

            # skip one candidate topics
            if len(variants) == 1:
                continue

            # get the offsets
            offsets = [self.candidates[v].offsets[0] for v in variants]

            # get the first occurring variant
            first = variants[offsets.index(min(offsets))]

            # find the nodes to which it connects -- Python 2/3 compatible
            # for start, end in self.graph.edges_iter(first):
            for start, end in self.graph.edges(first):

                boosters = []
                for v in variants:
                    if v != first and self.graph.has_edge(v, end):
                        boosters.append(self.graph[v][end]['weight'])

                if boosters:
                    weighted_edges[(start, end)] = np.sum(boosters)

        # update edge weights -- Python 2/3 compatible
        # for nodes, boosters in weighted_edges.iteritems():
        for nodes, boosters in weighted_edges.items():
            node_i, node_j = nodes
            position_i = 1.0 / (1 + self.candidates[node_i].offsets[0])
            position_i = math.exp(position_i)
            self.graph[node_j][node_i]['weight'] += (boosters*alpha*position_i)


    def candidate_weighting(self,
                            threshold=0.74,
                            method='average',
                            alpha=1.1):
        """ Candidate weight calculation using random walk.

            Args:
                threshold (float): the minimum similarity for clustering,
                    defaults to 0.25.
                method (str): the linkage method, defaults to average.
                alpha (float): hyper-parameter that controls the strength of the
                    weight adjustment, defaults to 1.1.
        """

        # cluster the candidates
        self.topic_clustering(threshold=threshold, method=method)

        # build the topic graph
        self.build_topic_graph()

        if alpha > 0.0:
            self.weight_adjustment(alpha)

        # compute the word scores using random walk
        self.weights = nx.pagerank_scipy(self.graph)

