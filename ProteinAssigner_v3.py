#!/usr/bin/python

# -*- coding: utf-8 -*-

# Module metadata variables
__author__ = "Rafael Barrero Rodriguez"
__credits__ = ["Rafael Barrero Rodriguez", "Jose Rodriguez", "Jesus Vazquez"]
__license__ = "Creative Commons Attribution-NonCommercial-NoDerivs 4.0 Unported License https://creativecommons.org/licenses/by-nc-nd/4.0/"
__version__ = "0.0.1"
__email__ = "rbarreror@cnic.es;jmrodriguezc@cnic.es"
__status__ = "Development"

# Primary libraries
import argparse
import multiprocessing
import logging
import os
import sys
import yaml
import concurrent.futures
import itertools
from itertools import repeat
import numpy as np
import pandas as pd
import re
import sys
from time import time


# Constants
progname = os.path.basename(sys.argv[0])
suffixScript = 'PA'

codingError = {
    -1: 'Error reading infile',
    -2: 'Error reading fasta',
    -3: 'Column with plain peptide was not found',
    -4: 'Column with candidate proteins was not found'
}

# Functions and Classes

def readDF(filePath):
    '''
    '''
    df_tmp = pd.read_csv(filePath, sep='\t', float_precision='high', low_memory=False)
    df_tmp['_filePaths'] = filePath
    return df_tmp

def readIDQ(paramsDict):
    '''
    '''
    with concurrent.futures.ProcessPoolExecutor(max_workers=int(paramsDict['n_cores'])) as executor:
        df_list = executor.map(readDF, paramsDict['infile'])
   
    df = pd.concat(df_list, axis=0).reset_index(drop=True)
    return df

def getAccession(line, decoyPrefix):
    '''
    '''
    isTarget = True

    try:
        pre_acc_RE = re.search(r'^>([^|]*)\|([^|]+)\|', line)
        if pre_acc_RE != None:
            preffix, accession = pre_acc_RE.groups()
        else:
            if re.search(r'^>'+re.escape(decoyPrefix), line) and decoyPrefix!="":
                return line[1:], False
            else:
                return line[1:], True

    except:
        logging.exception(f'Error when extracting accession from fasta:\n{line}')

    # if accession comes from decoy protein, add decoy to the accession (not to confuse with real protein)
    if re.search(r'^'+re.escape(decoyPrefix), preffix) and decoyPrefix!="":
        isTarget = False
        accession = decoyPrefix + accession
    
    return accession, isTarget
    

def replaceLeu(seq_i, iso_leucine):
    '''
    '''
    seq_o = seq_i

    for i in ['L', 'I', 'J']:
        if i == iso_leucine: continue
        if iso_leucine != "": seq_o = seq_o.replace(i, iso_leucine)
    
    return seq_o


def fastaReader(paramsDict):
    '''
    '''
    acc_list = []
    desc_list = []
    seq_list = []
    isTarget_list = []

    with open(paramsDict["fasta_params"]['fasta'], 'r') as f:

        seq_i = ""
        for line in f:

            if '>' in line[0]:

                if seq_i != '':
                    seq_list.append(seq_i)
                    seq_i = ''
                
                desc_list.append(line.strip()[1:])
                accession, isTarget = getAccession(line.strip(), paramsDict["fasta_params"]['decoy_prefix'])
                acc_list.append(accession)
                isTarget_list.append(isTarget)
            
            else:
                seq_i += line.strip()
        
        seq_list.append(seq_i)
        
        seq_list = [replaceLeu(i, paramsDict["fasta_params"]['iso_leucine']) for i in seq_list]
    
    acc_desc_seq = list(zip(desc_list, acc_list, seq_list, isTarget_list))
    target_desc_acc_seq = [(i,j,k) for i,j,k,l in acc_desc_seq if l]
    target_desc_acc_seq = {
        'd':[i for i,j,k in target_desc_acc_seq], 
        'acc':[j for i,j,k in target_desc_acc_seq], 
        'seq':[k for i,j,k in target_desc_acc_seq]
    }

    decoy_desc_acc_seq = [(i,j,k) for i,j,k,l in acc_desc_seq if not l]
    decoy_desc_acc_seq = {
        'd':[i for i,j,k in decoy_desc_acc_seq], 
        'acc':[j for i,j,k in decoy_desc_acc_seq], 
        'seq':[k for i,j,k in decoy_desc_acc_seq]
    }

    #return acc_desc_seq
    #return acc_list, desc_list, seq_list
    return target_desc_acc_seq, decoy_desc_acc_seq


def getCandidateProteins_in(q, pp_set, paramsDict):
    '''
    '''

    # Split pp_set in chunks
    pp_set_chunks = split(pp_set, int(paramsDict['n_cores']))

    with concurrent.futures.ProcessPoolExecutor(max_workers=int(paramsDict['n_cores'])) as executor:
        sub_seqs = list(executor.map(pp_set_in_prot, pp_set_chunks, repeat(q['seq'])))
        sub_seqs = list(executor.map(pp_seq_in_acc_d, sub_seqs, repeat(q['acc']), repeat(q['d']))) 
    
    sub_seqs = add_flatten_lists(sub_seqs)

    return sub_seqs


def split(a, n):
    k, m = divmod(len(a), n)
    return (a[i*k+min(i, m):(i+1)*k+min(i+1, m)] for i in range(n))


def pp_set_in_prot(pp_set, seq_list):
    return [[i in j for j in seq_list] for i in pp_set]


def pp_seq_in_acc_d(sub_seqs, acc_list, d_list):
    return [[' // '.join(j) for j in list(zip(*filter(lambda x: x[0], zip(i, acc_list, d_list))))[1:]] for i in sub_seqs]


def add_flatten_lists(the_lists):
    result = []
    for _list in the_lists:
        result += _list
    return result


def getMostProbableProtein(dffile, paramsDict):
    '''
    
    '''
    # Columns containing information from multiple protein separated by ";". Name of columns got from "col" variable in main
    #semicolon_col_list = paramsDict['_additional_column'] #["ProteinsLength","NpMiss","NpTotal","PeptidePos","PrevNextaa"]

    # Name of the column with protein information (it can be UniProtIDs or Proteins)
    sc_prot_col = paramsDict["column_params"]['prot_column'][0]

    # Get indexes of dffile
    df_index = dffile.index.to_list()

    # This array will contain the position of the most probable protein in each row
    df_index_result_pos = np.ones_like(df_index)*(-1)

    # Get boolean with non decoy PSMs (has no real protein assigned)
    non_decoy_bool = ~dffile[sc_prot_col].isnull()

    # Extract sc_prot_col from dffile. Generate List of Lists [ [p1, p2...], [p1, p2..] ... ]
    if paramsDict['mode'] == 'column':
        # JUAN ANTONIO inputs contain DECOY_sp and DECOY_tr. They have advantage...
        # remove blank elements of the split
        sc_prot_list = [list([j.strip() for j in i.split(paramsDict["column_params"]['sep_char']) if j.strip()!='']) for i in dffile.loc[non_decoy_bool, sc_prot_col].to_list()]
        
        # sc_prot_list contains a list with sets of (valid) candidate proteins. 
        # sc_prot_list_index contains a list with sets of index of these valid candidate proteins (excluding "" and decoys)
        #import pdb; pdb.set_trace()
        """
        l2 = []
        import pdb
        try:
            for i in sc_prot_list:
                l1 = []
                for n,j in enumerate(i):
                    if 'decoy' in j.lower(): continue
                    l1.append([j,n])
            
                l2.append(list(zip(*l1)))
            #try:
            r1, r2 = list(zip(*l2[39000:39062]))
        except:
            print("Hola?")
            pdb.set_trace()

        pdb.set_trace()
        """
        # if only contain decoy it will be []
        sc_prot_list_index_pair = [list(zip(*[[j,n] for n,j in enumerate(i) if 'decoy' not in j.lower()])) for i in sc_prot_list]
        # replace [] by [(decoy,), (0,)]
        sc_prot_list_index = [i if i != [] else [("DECOY_",), (0,)] for i in sc_prot_list_index_pair]
        sc_prot_list, sc_prot_list_index = list(zip(*sc_prot_list_index))
        #sc_prot_list, sc_prot_list_index = list(zip(*[zip(*[[j,n] for n,j in enumerate(i) if 'decoy' not in j.lower()]) for i in sc_prot_list]))

        # in this cool way we generate a list with size of the initial table, with sc_prot_list_index in the right position
        c = itertools.count(0)
        all_prot_list_index = [sc_prot_list_index[next(c)] if i else [] for i in non_decoy_bool]   

    else:
        sc_prot_list = [tuple([j.strip() for j in i.split(paramsDict["column_params"]['sep_char']) if j.strip()!='']) for i in dffile.loc[non_decoy_bool, sc_prot_col].to_list()]

    # Extract peptide sequence of each scan: pepcolumn of dffile
    p_seq_list = dffile.loc[non_decoy_bool, paramsDict['seq_column']].to_list()

    # Get set of pairs (peptide sequence, [protein list]). Do not repeat peptide sequence!
    pseq_prot_pair_list = list(set(list(zip(p_seq_list, sc_prot_list))))
    
    # Get flat list with all proteins contributed by each peptide sequence to get number of peptides per protein
    protein_from_pseq = sorted([j for i in pseq_prot_pair_list for j in i[1]])
    protein2npep = {k : len(list(g)) for k, g in itertools.groupby(protein_from_pseq)}

    # Get flat list with all proteins contributed by each scan to get number of scans per protein
    protein_from_scan = sorted([j for i in sc_prot_list for j in i])
    protein2nscan = {k : len(list(g)) for k, g in itertools.groupby(protein_from_scan)}

    # Extract elements of sc_prot_list with more than one protein
    sc_prot_list_number = np.array([len(i) for i in sc_prot_list])
    sc_prot_gt1_bool_arr = sc_prot_list_number > 1
    sc_prot_gt1_list = [i for i,j in zip(sc_prot_list, sc_prot_gt1_bool_arr.tolist()) if j]

    
    # Resolve elements with one protein only: From full index, get non-decoy. And from them, get those with one protein
    # aux_arr is analogous to non-decoy elements. We first index those with one protein only.
    # aux_arr is subset of df_index_result_pos --> aux_arr = df_index_result_pos[non_decoy_bool]
    
    aux_arr = np.ones_like(np.arange(0, len(sc_prot_list)))*(-1)
    aux_arr[~sc_prot_gt1_bool_arr] = 0 # protein in position 0 for elements with 0 or 1 proteins
    aux_arr[sc_prot_list_number==0] = -1 # -1 for elements with 0 protein (case of all decoys)
    
    # aux_arr2 is analogous to elements with more than one protein. We first index those with only one maximum
    # aux_arr2 is a subset of aux_arr --> aux_arr2 = aux_arr[sc_prot_gt1_bool_arr]
    aux_arr2 = np.ones_like(np.arange(0, len(sc_prot_gt1_list)))*(-1)

    
    # Replace protein by its number of peptides
    sc_prot_npep_gt1_list = [[protein2npep[j] for j in i] for i in sc_prot_gt1_list]

    # Get List of pairs (index of elem, position of maximum protein)
    sc_prot_npep_gt1_1max = [[n, np.argmax(i)] for n, i in enumerate(sc_prot_npep_gt1_list) if np.sum(np.max(i) == np.array(i)) == 1]
    sc_prot_npep_gt1_1max_index = [i for i,j in sc_prot_npep_gt1_1max]
    sc_prot_npep_gt1_1max_position = [j for i,j in sc_prot_npep_gt1_1max]
    
    # Add solved position to aux_arr2
    aux_arr2[sc_prot_npep_gt1_1max_index] = sc_prot_npep_gt1_1max_position

    # Resolve element with more than one maximum. We now use number of scans instead of number of peptides
    sc_prot_npep_gt1_gt1max_index = np.arange(0, len(sc_prot_gt1_list))
    sc_prot_npep_gt1_gt1max_index[sc_prot_npep_gt1_1max_index] = -1
    sc_prot_npep_gt1_gt1max_index = sc_prot_npep_gt1_gt1max_index[sc_prot_npep_gt1_gt1max_index != -1].tolist()

    sc_prot_nscan_gt1_list = \
        [[protein2nscan[j] for j in sc_prot_gt1_list[i]] for i in sc_prot_npep_gt1_gt1max_index]

    sc_prot_nscan_gt1_1max = [np.argmax(i) for i in sc_prot_nscan_gt1_list] #if np.sum(np.max(i) == np.array(i)) == 1]

    # Add to aux_arr2, the position of elements resolved using PSMs (or arbitrary position)
    aux_arr2[sc_prot_npep_gt1_gt1max_index] = sc_prot_nscan_gt1_1max

    # Incorporate to aux_arr its subset aux_arr2
    aux_arr[sc_prot_gt1_bool_arr] = aux_arr2

    # Incorporate to df_index_result_pos its subset aux_arr
    df_index_result_pos[non_decoy_bool] = aux_arr

    # Generate new columns
    mppSuffix = "_MPP"
    
    workingColumns = paramsDict["column_params"]["prot_column"] #paramsDict['_additional_column']+[paramsDict['prot_column']]
    if paramsDict['mode']=='column':
        # JUAN ANTONIO case...
        new_columns_list = [
            [l.strip() for l in j.split(paramsDict["column_params"]['sep_char']) if l.strip()!='' and 'decoy' not in l.strip().lower()] \
                for j in dffile[sc_prot_col].to_list()
            ]

        new_columns_list = [i if i !=[] else ["DECOY_"] for i in new_columns_list]

        new_columns_list = [
            [[l for l in j][k] if k!=-1 else "" for j,k in zip(new_columns_list, df_index_result_pos)]
            ]

        #new_columns_list = [
        #    [[l.strip() for l in j.split(paramsDict["column_params"]['sep_char']) if l.strip()!='' and 'decoy' not in l.strip().lower()][k] if k!=-1 else "" \
        #        for j,k in zip(dffile[sc_prot_col].to_list(), df_index_result_pos)]
        #    ]
        
        if len(paramsDict["column_params"]["prot_column"]) > 1:
            # split candidate proteins removing blank space
            new_columns_list_2 = [[[l.strip() for l in j.split(paramsDict["column_params"]['sep_char']) if l.strip()!=''] for j in dffile[i].to_list()] \
                for i in paramsDict["column_params"]["prot_column"][1:]]

            # extract non decoys 
            new_columns_list_2 = [[np.array(j1)[list(j2)].tolist() if j2!=[] else j1 for j1,j2 in zip(i,all_prot_list_index)] 
                for i in new_columns_list_2]

            # extract MPP
            new_columns_list_2 = [[j[k] if k!=-1 else "" for j,k in zip(i, df_index_result_pos)] for i in new_columns_list_2]

            new_columns_list_3 = []
            for i in new_columns_list_2:
                j = np.array(i)
                j[np.array(new_columns_list[0])=="DECOY_"] = "DECOY_"
                new_columns_list_3.append(j.tolist())
            
            new_columns_list += new_columns_list_3

    else:
        new_columns_list = [[[l.strip() for l in j.split(paramsDict["column_params"]['sep_char']) if l.strip()!=''][k] if k!=-1 else "" for j,k in zip(dffile[i].to_list(), df_index_result_pos)] \
            for i in workingColumns]

    new_columns_df = pd.DataFrame({i+mppSuffix: j for i,j in zip(workingColumns, new_columns_list)})

    # Remove > from the first character in protein column
    #if semicolon_col_protein_list[0]+mppSuffix in new_columns_df.columns:
    #    new_columns_df[semicolon_col_protein_list[0]+mppSuffix] = new_columns_df[semicolon_col_protein_list[0]+mppSuffix].str.replace('^>', '', regex=True)
    
    
    # Generate final dataframe
    dffile_MPP = pd.concat([dffile.reset_index(drop=True), new_columns_df.reset_index(drop=True)], axis=1)

    # Replace " // " for ";"
    if paramsDict["mode"].lower()=="fasta":
        for i in workingColumns:
            dffile_MPP[i] = dffile_MPP[i].str.replace(paramsDict["column_params"]['sep_char'], ';')

    return dffile_MPP


def writeDF(filePath, df):
    df_i = df.loc[df['_filePaths'] == filePath, :].copy()
    df_i.dropna(axis=1, how='all', inplace=True)
    df_i.drop(labels='_filePaths', axis=1, inplace=True)
    outFilePath = f"{os.path.splitext(filePath)[0]}_{suffixScript}{os.path.splitext(filePath)[1]}"
    df_i.to_csv(outFilePath, sep="\t", index=False)

def writeIDQ(df, paramsDict):
    '''
    '''
    with concurrent.futures.ProcessPoolExecutor(max_workers=int(paramsDict['n_cores'])) as executor:
        executor.map(writeDF, paramsDict['infile'], repeat(df))

# Main
def main(paramsDict):
    '''
    '''

    #
    # Read ID Table
    #
    t = time()
    try:
        df = readIDQ(paramsDict)    
        logging.info(f'ID tables were read in {str(round(time()-t, 2))}s')
    except:
        logging.exception(f'Error reading input files: {paramsDict["infile"]}')
        sys.exit(-1)

    if not paramsDict['seq_column'] in df.columns:
        logging.error(f'{paramsDict["seq_column"]} column not found')
        sys.exit(-3)

    
    #
    # Create column with candidate proteins
    #
    if paramsDict['mode'].lower() == 'fasta':

        # read fasta
        t=time()
        try:
            target_q, decoy_q = fastaReader(paramsDict)
            logging.info(f'Fasta was read in {str(round(time()-t, 2))}s: {paramsDict["fasta_params"]["fasta"]}')
        except:
            logging.exception(f'Error reading fasta file: {paramsDict["fasta_params"]["fasta"]}')
            sys.exit(-2)
        

        # Identify candidate proteins
        logging.info(f'Identifying candidate proteins...')

        # Extract plain peptides (pp) from psm table
        pp_psm = df[paramsDict['seq_column']].to_list() # list of pp of each psm
        pp_psm_index = sorted(list(zip(pp_psm, list(range(len(pp_psm)))))) # list of pp_psm with its index
        pp_indexes = [(i, tuple([l for k,l in j])) for i,j in itertools.groupby(pp_psm_index, lambda x: x[0])] # pp_set with all their indexes
        pp_set = sorted(list(set(pp_psm))) # pp_set

        # Find plain peptides in target
        t = time()
        pp_acc_d = getCandidateProteins_in(target_q, pp_set, paramsDict)
        logging.info(f'Plain peptides were searched in target proteins {str(round(time()-t, 2))}s')

        # Find the rest of the plain peptides in decoy
        pp_decoy_index = list(zip(*[[i,k] for i,j,k in zip(pp_set, pp_acc_d, range(len(pp_set))) if j==[]]))
        if pp_decoy_index != []:
            t = time()
            pp_decoy, pp_decoy_index = pp_decoy_index
            pp_decoy_acc_d = getCandidateProteins_in(decoy_q, pp_decoy, paramsDict)
        
            for i,j in zip(pp_decoy_acc_d, pp_decoy_index): 
                pp_acc_d[j] = i if i!=[] else ['','']
        
            logging.info(f'Remaining plain peptides were searched in decoy proteins {str(round(time()-t, 2))}s')


        # Add to df the columns with accession and description of candidate proteins (!!! Do not overwrite columns)
        pp_indexes_acc, pp_indexes_d = zip(*[[(i[1], (j[0],)), (i[1], (j[1],))] for i,j in zip(pp_indexes, pp_acc_d)])        
        acc_column = list(zip(*sorted([j for i in pp_indexes_acc for j in itertools.product(*i)])))[1]
        d_column = list(zip(*sorted([j for i in pp_indexes_d for j in itertools.product(*i)])))[1]

        d_colName, acc_colName = suffixScript+'_description', suffixScript+'_accession'

        # get times these columns appear
        colNumber = np.max(np.sum(list(zip(*[(d_colName in i, acc_colName in i) for i in df.columns])), axis=1))+1
        d_colName += f"_{colNumber}"
        acc_colName += f"_{colNumber}"

        # add these new columns
        df[d_colName] = d_column
        df[acc_colName] = acc_column
        logging.info(f"{d_colName} and {acc_colName} columns with candidate proteins was created")

        # If column params section is note created, add it (it is used in MPP calculation)
        if "column_params" not in paramsDict.keys(): 
            paramsDict["column_params"] = {}

        paramsDict["column_params"]['prot_column'] = [acc_colName, d_colName] # column used to calculate most probable protein
        #paramsDict['_additional_column'] = [d_colName] # another from which extract information of most probable protein
        paramsDict['_replace_delim'] = True # Protein delimiter is " // ". We want to change it to ; in the end (but only in fasta mode)
        paramsDict["column_params"]['sep_char'] = " // "


    #
    # Calculate most probable protein
    #
    logging.info(f'Calculating most probable protein...')
    if np.any([i not in df.columns for i in paramsDict["column_params"]['prot_column']]):
        logging.error(f'{paramsDict["column_params"]["prot_column"]} columns not found')
        sys.exit(-4)

    t = time()
    df = getMostProbableProtein(df, paramsDict)
    logging.info(f'Most probable protein was calculated in {str(round(time()-t, 2))}s')

    #
    # Write ID table
    #
    t = time()
    logging.info("Writing output tables...")
    writeIDQ(df, paramsDict)
    logging.info(f'Output tables were written in {str(round(time()-t, 2))}s')

    return 0


if __name__ == '__main__':

    multiprocessing.freeze_support()
    
    # Parse arguments
    parser = argparse.ArgumentParser(
            description='Calculate most probable protein assigned to each PSM ',
            #formatter_class=argparse.RawDescriptionHelpFormatter,
            formatter_class=argparse.RawTextHelpFormatter,
            epilog=f'''\
Created 2021-11-24, Rafael Barrero Rodriguez

Usage:
    {progname} -c "path to YAML config file"
''')


    # Parse command-line arguments
    parser.add_argument('-c', '--config', dest='config', metavar='FILE', type=str,
        help='Path to YAML file containing parameters')
    
    args = parser.parse_args()

    
    # Read YAML
    with open(args.config, 'r') as f:
        try:
            paramsDict = yaml.safe_load(f)
        except yaml.YAMLError as exc:
            print(exc)
            sys.exit(-1000)
            
    
    logFile = os.path.splitext(paramsDict["infile"][0])[0]
    logFile += f"_{suffixScript}.log"
    #logFile = os.path.join('PA_logs', datetime.now().strftime("%Y%m%d%H%M%S")+'.log')#logFile
    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s - %(levelname)s - %(message)s',
                        datefmt='%m/%d/%Y %I:%M:%S %p',
                        handlers=[logging.FileHandler(logFile),
                                    #logging.StreamHandler(self.stream),
                                    logging.StreamHandler()])

    
    logging.info('Start script: '+"{0}".format(" ".join([x for x in sys.argv])))
    t0 = time()
    main(paramsDict)
    m, s = divmod(time()-t0,60)
    logging.info(f'End script: {m}m and {round(s,2)}s') 