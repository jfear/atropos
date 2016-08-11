# coding: utf-8
"""
Alignment module.
"""
from atropos._align import Aligner, compare_prefixes, locate

# flags for global alignment

# The interpretation of the first flag is:
# An initial portion of seq1 may be skipped at no cost.
# This is equivalent to saying that in the alignment,
# gaps in the beginning of seq2 are free.
#
# The other flags have an equivalent meaning.
START_WITHIN_SEQ1 = 1
START_WITHIN_SEQ2 = 2
STOP_WITHIN_SEQ1 = 4
STOP_WITHIN_SEQ2 = 8

# Use this to get regular semiglobal alignment
# (all gaps in the beginning or end are free)
SEMIGLOBAL = START_WITHIN_SEQ1 | START_WITHIN_SEQ2 | STOP_WITHIN_SEQ1 | STOP_WITHIN_SEQ2


def compare_suffixes(s1, s2, wildcard_ref=False, wildcard_query=False):
    """
    Find out whether one string is the suffix of the other one, allowing
    mismatches. Used to find an anchored 3' adapter when no indels are allowed.
    """
    s1 = s1[::-1]
    s2 = s2[::-1]
    _, length, _, _, matches, errors = compare_prefixes(s1, s2, wildcard_ref, wildcard_query)
    return (len(s1) - length, len(s1), len(s2) - length, len(s2), matches, errors)

# Alternative semi-global alignment (
# http://www.bioinf.uni-freiburg.de/Lehre/Courses/2013_SS/V_Bioinformatik_1/lecture4.pdf)
# strategies designed to improve insert matching of paired-end reads.
#
# Note: these are currently just prototype implementations. They will need to be optimized
# using numpy and/or re-written in cython.
#
# 1. SeqPrep algorithm: insert match algorithm that performs thresholded exhaustive
#    comparison to minimize probability of incorrect alignment. Relies on the fact that
#    overlapping reads share alleles and indels (i.e. no gaps are required) (in C++).
#    https://github.com/imgag/ngs-bits/tree/master/src/SeqPurge.
# 2. Skewer algorithm: bit-masked k-difference matching (in C++).
#    https://github.com/relipmoc/skewer
# 3. Quality-aware overlap alignment (in Haskell).
#    https://hackage.haskell.org/package/bio-0.5.3/docs/Bio-Alignment-QAlign.html
# 4. FOGSAA, modified for semi-global alignment.
#    http://www.nature.com/articles/srep01746
#    http://www.isical.ac.in/~bioinfo_miu/FOGSAA.7z

from bitarray import bitarray
import math

class FactorialCache(object):
    def __init__(self, init_size=150):
        self.factorials = [1] * init_size
        self.max_n = 1
        self.cur_array_size = init_size
        
    def factorial(self, n):
        if n > self.max_n:
            if n >= self.cur_array_size:
                self._extend(n)
            self._fill_upto(n)
        return self.factorials[n]
        
    def _extend(self, n):
        extension_size = n - self.cur_array_size + 1
        self.factorials += [1] * extension_size
    
    def _fill_upto(self, n):
        i = self.max_n
        next_i = i + 1
        while i < n:
            self.factorials[next_i] = next_i * self.factorials[i]
            i = next_i
            next_i += 1
        self.max_n = i

BASE_ENCODING = dict(
    A=bitarray('1000'),
    C=bitarray('0100'),
    G=bitarray('0010'),
    T=bitarray('0001'),
    N=bitarray('1111')
)

class OneHotEncoded:
    def __init__(self, seq=None, bits=None, ambig=None):
        if seq is not None:
            seq = seq.upper()
            self.size = len(seq)
            self.bits = bitarray()
            self.bits.encode(BASE_ENCODING, seq)
            self.ambig = bitarray(''.join('1' if b == 'N' else '0' for b in seq))
        else:
            self.size = len(bits) // 4
            self.bits = bits
            self.ambig = ambig
    
    def __len__(self):
        return self.size
    
    def __getitem__(self, key):
        bits, ambig = self._subseq(key.start, key.stop)
        return OneHotEncoded(bits=bits, ambig=ambig)
    
    def compare(self, other, offset=None, overlap=None, ambig_match=None):
        """
        Returns the number of matching bases.
        offset: the number of bases to move self forward from the left
                relative to other before comparing.
        overlap: the number of bases to move self backward from the right
                relative to other before comparing.
        ambig_match: whether to count ambiguous characters as matches (True),
                     mismatches (False) or ignore them (None).
        
        Offset and overalap are mutually exclusive. The diagram below shows
        how they work. Comparsions are made on the sequences between the
        pipes.
        
        offset > 0
        
        ..offset..|-------seq1--------|----------
        ----------|-------seq2--------|..offset..
        
        overlap > 0
                      |--overlap--|-----seq1------
        ------seq2----|--overlap--|
        """
        l1 = len(self)
        l2 = len(other)
        size = min(l1, l2)
        if offset is None and overlap is None and (l1 == l2):
            eq = self.bits & other.bits
            ambig1 = self.ambig
            ambig2 = other.ambig
            size = l1
        else:
            start = 0
            if offset:
                assert offset < size
                start = offset
                size = size - offset
            elif overlap:
                assert overlap < size
                start = size - overlap
                size = overlap
            bits1, ambig1 = self._subseq(0, size)
            bits2, ambig2 = other._subseq(start, start + size)
            eq = bits1 & bits2
        
        # when ambiguous bases match, they contribute 4 counts rather than 1
        matches = eq.count() - ((ambig1 & ambig2).count() * 3)
        if ambig_match is True:
            return (matches, size)
        else:
            num_ambig = (ambig1 | ambig2).count()
            matches -= num_ambig
            if ambig_match is False:
                return (matches, size)
            else:
                return (matches, size - num_ambig)
    
    def _subseq(self, start, stop):
        return (
            self.bits[(start*4):((stop*4) + 1)],
            self.ambig[start:stop]
        )
    
    def reverse_complement(self):
        self.bits.reverse()
        self.ambig.reverse()
                
    def to_acgt(self):
        return self.bits.decode(BASE_ENCODING)
    
    def translate(self):
        binstr = self.bits.to01()
        basestr = self.to_acgt()
        return ((binstr[(i*4):((i+1)*4)], basestr[i])
            for i in range(len(self)))
    
    def __repr__(self):
        return "".join(self.to_acgt())
    
    def __str__(self):
        return "ACGT\n----\n" + "\n".join("{} : {}".format(*t) for t in self.translate())

def match_probability(matches, size, factorial_cache):
    nfac = factorial_cache.factorial(size)
    p = 0.0
    i = matches
    
    while i <= size:
        p += (
            (0.75 ** (size - i)) *
            (0.25 ** i) *
            nfac /
            factorial_cache.factorial(i) /
            factorial_cache.factorial(size - i)
        )
        i += 1

    return p

class SeqPurgeAligner(object):
    """
    Implementation of the SeqPurge insert matching algorithm.
    This only works with paired-end reads with 3' adapters.
    """
    def __init__(self, fw_adapter, rv_adapter, max_error_prob=1E-6,
                 min_insert_overlap=1, min_insert_match_frac=0.8,
                 min_adapter_overlap=1, min_adapter_match_frac=0.8,
                 adapter_check_cutoff=9):
        self.fw = OneHotEncoded(fw_adapter)
        self.rv = OneHotEncoded(rv_adapter)
        self.rv.reverse_complement()
        self.min_insert_match_frac = min_insert_match_frac
        self.max_error_prob = max_error_prob
        self.min_adapter_overlap = min_adapter_overlap
        self.min_adapter_match_frac = min_adapter_match_frac
        self.adapter_check_cutoff = adapter_check_cutoff
        self.factorial_cache = FactorialCache()
    
    def match(self, seq1, seq2):
        l1 = len(seq1)
        l2 = len(seq2)
        seq_len = min(l1, l2)
        if l1 > l2:
            seq1 = seq1[:l2]
        elif l2 > l1:
            seq2 = seq1[:l1]
        
        seq1 = OneHotEncoded(seq1)
        seq2 = OneHotEncoded(seq2)
        seq2.reverse_complement()
        
        best_offset = None
        best_p = 1.0
        
        for offset in range(seq_len):
            # Count number of matches between the two sequences.
            # size = total number of bases compared (excluding Ns)
            matches, size = seq1.compare(seq2, offset)

            # Check that at least 1 base was compared and that there are fewer
            # than the maximium number of allowed mismatches
            if size < min_insert_overlap or matches >= round(float(seq_len - offset) * self.min_insert_match_frac):
                continue

            # Compute probability of seeing this number of matches at random and
            # make sure it's less than the maximum allowed
            p = match_probability(matches, size)
            if p > self.max_error_prob:
                continue
            
            # Match the overhang against the adapter sequence
            if offset > self.adapter_check_cutoff:
                a1_match = seq1.compare(self.fw, overlap=offset)
                a1_prob = match_probability(*a1_match)
                a2_match = self.rv.compare(seq2, overlap=offset)
                a2_prob = match_probability(*a2_match)
                if (a1_prob * a2_prob) > self.max_error_prob:
                    continue
            else:
                min_adapter_matches = math.ceil(offset * self.min_adapter_match_frac)
                a1_match = seq1.compare(self.fw, overlap=offset)
                if a1_match[0] < min_adapter_matches:
                    a2_match = self.rv.compare(seq2, overlap=offset)
                    if a2_match[0] < min_adapter_matches:
                        continue
            
            if p < best_p:
                best_p = p
                best_offset = offset
        
        # If we didn't find a match, or if the best match is too short, return None
        if best_offset is None or best_offset < self.min_adapter_overlap:
            return None
        
        # Otherwise, return the adapter start position in each read
        return seq_len - offset
