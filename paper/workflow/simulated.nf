#!/usr/bin/env nextflow

/* Atropos paper workflow for simulated DNA-Seq reads
 * --------------------------------------------------
 * Configuration is externalized into separate files for desktop and cluster.
 * The main difference is that Docker is used to manage images on desktop, 
 * verusus Singularity on the cluster.
 *
 * The read data is contained within a Docker container. During local execution, 
 * we attach that container directly to the tool container (using --from-volumes 
 * <data container>). Singularity does not allow for this, so when running on 
 * the cluster we use a separate process to unpack the data from the container 
 * into $params.storeDir. Note that storeDir persists beyond the end of the 
 * script, so we delete it in the shell script that runs the workflow.
 *
 * Parameters
 * ----------
 * The following are expected to be defined in params:
 * - threadCounts: The numbers of threads (cpus) to test
 * - errorRates: The error rates of the simulated data sets
 * - minLength: minimum read length
 * - batchSize: read batch size
 * - quals: quality thresholds for trimming
 * - aligners: Atropos aligner algorithms to test
 * - compressionSchemes: Atropos compression schemes to test
 * - adapter1, adapter2: Adapter sequence
 */

// variables for all tools
params.errorRates = [ '001', '005', '01' ]
params.quals = [ 0 ]
params.adapter1 = "AGATCGGAAGAGCACACGTCTGAACTCCAGTCACACAGTGATCTCGTATGCCGTCTTCTGCTTG"
params.adapter2 = "AGATCGGAAGAGCGTCGTGTAGGGAAAGAGTGTAGATCTCGGTGGTCGCCGTATCATT"
params.minLength = 25
params.batchSize = 5000

// atropos-specific variables
params.aligners = [ 'insert', 'adapter' ]
params.compressionSchemes = [ 'worker', 'writer', 'nowriter' ]

process Extract {
  container "jdidion/atropos_simulated"
  
  input:
  each err from params.errorRates
  
  output:
  set err, "sim_${err}.{1,2}.fq", "sim_${err}.{1,2}.aln" into simReads
  
  script:
  """
  cp \
    /data/simulated/sim_${err}.1.fq  \
    /data/simulated/sim_${err}.2.fq  \
    /data/simulated/sim_${err}.1.aln \
    /data/simulated/sim_${err}.2.aln \
    .
  """
}

process Atropos {
  tag { "atropos_${task.cpus}_${err}_q${qcut}_${aligner}_${compression}" }
  cpus { threads }
  container "jdidion/atropos_paper"

  input:
  set err, file(reads), file(alns) from simReads
  each threads from params.threadCounts
  each qcut from params.quals
  each aligner from params.aligners
  each compression from params.compressionSchemes
  
  output:
  set val("${task.tag}"), err, alns, file("${task.tag}.{1,2}.fq.gz") into trimmedAtropos
  set val("${task.tag}"), file("${task.tag}.timing.txt") into timingAtropos
  file "${task.tag}.report.txt" into reportFile
  
  script:
  """
  /usr/bin/time -v -o ${task.tag}.timing.txt atropos \
    -T $task.cpus --aligner $aligner --op-order GACQW \
    -a $params.adapter1 -A $params.adapter2 -q $qcut --trim-n \
    -m $params.minLength --batch-size $params.batchSize \
    --no-default-adapters --no-cache-adapters --log-level ERROR --quiet \
    --insert-match-error-rate 0.20 -e 0.10 \
    -o ${task.tag}.1.fq.gz -p ${task.tag}.2.fq.gz  \
    --report-file ${task.tag}.report.txt --quiet \
    $task.ext.compressionArg -pe1 ${reads[0]} -pe2 ${reads[1]}
  """
}

process Skewer {
  tag { "skewer_${task.cpus}_${err}_q${qcut}" }
  cpus { threads }
  container "jdidion/skewer"

  input:
  set err, file(reads), file(alns) from simReads
  each threads from params.threadCounts
  each err from params.errorRates
  each qcut from params.quals

  output:
  set val("${task.tag}"), err, alns, file("${task.tag}.{1,2}.fq.gz") into trimmedSkewer
  set val("${task.tag}"), file("${task.tag}.timing.txt") into timingSkewer

  script:
  """
  >&2 echo ${task.tag} && \
  /usr/bin/time -v skewer \
    -m pe -l $params.minLength -match_perc 80 \
    -o $task.tag -z --quiet \
    -x $params.adapter1 -y $params.adapter2 -t $task.cpus \
    -q $qcut -n ${reads[0]} ${reads[1]} \
    > ${task.tag}.report.txt \
    2>> ${task.tag}.timing.txt && \
  mv ${task.tag}-trimmed-pair1.fastq.gz ${task.tag}.1.fq.gz && \
  mv ${task.tag}-trimmed-pair2.fastq.gz ${task.tag}.2.fq.gz
  """
}

process SeqPurge {
  tag { "seqpurge_${task.cpus}_${err}_q${qcut}" }
  cpus { threads }
  container "jdidion/seqpurge"

  input:
  set err, file(reads), file(alns) from simReads
  each threads from params.threadCounts
  each err from params.errorRates
  each qcut from params.quals

  output:
  set val("${task.tag}"), err, alns, file("${task.tag}.{1,2}.fq.gz") into trimmedSeqPurge
  set val("${task.tag}"), file("${task.tag}.timing.txt") into timingSeqPurge
  file "${task.tag}.report.txt" into reportFile
  
  script:
  """
  /usr/bin/time -v -o ${task.tag}.timing.txt SeqPurge \
    -in1 ${reads[0]} -in2 ${reads[1]} \
    -out1 ${task.tag}-trimmed-pair1.pair1.fastq.gz \
    -out2 ${task.tag}-trimmed-pair1.pair2.fastq.gz \
    -a1 $params.adapter1 -a2 $params.adapter2 \
    -qcut $qcut -min_len $params.minLength \
    -task.cpus $task.cpus -prefetch $params.batchSize \
    -r 0.20 -summary $reportFile
  """
}

process AdapterRemoval {
  tag { "adapterremoval_${task.cpus}_${err}_q${qcut}" }
  cpus { threads }
  container "jdidion/adapterremoval"

  input:
  set err, file(reads), file(alns) from simReads
  each threads from params.threadCounts
  each err from params.errorRates
  each qcut from params.quals

  output:
  set val("${task.tag}"), err, alns, file("${task.tag}.{1,2}.fq.gz") into trimmedAdapterRemoval
  set val("${task.tag}"), file("${task.tag}.timing.txt") into timingAdapterRemoval
  file "${task.tag}.report.txt" into reportFile
  
  script:
  """
  usr/bin/time -v -o ${task.tag}.timing.txt AdapterRemoval \
    --file1 ${reads[0]} --file2 ${reads[1]} \
    --output1 ${task.tag}.1.fq.gz --output2 ${task.tag}.2.fq.gz --gzip \
    --adapter1 $params.adapter1 --adapter2 $params.adapter2 \
    --trimns --trimqualities --minquality $qcut \
    --minLengthgth $params.minLength --task.cpus $task.cpus
  """
}

// Concatenate all of the results into a single channel.
// If you add a tool process, make sure to add the stdout channel
// into the concat list here.

Channel
  .empty()
  .concat(
    trimmedAtropos,
    trimmedSkewer,
    trimmedSeqPurge,
    trimmedAdapterRemoval
  )
  .set { timingMerged }

process ParseTiming {
    container "jdidion/python_bash"
    
    input:
    set val(item), file(timing) from timingMerged
    
    output:
    stdout timingParsed
    
    script:
    """
    parse_gtime.py -i $timing -p $item
    """
}

process ShowPerformance {
    container "jdidion/python_bash"
    
    input:
    val parsedRows from timingParsed.toList()
    
    output:
    file "timing.tex"
    file "timing.svg"
    
    script:
    data = parsedRows.join("")
    """
    echo '$data' | show_performance.py -n foo -c bar -o timing -f tex svg pickle
    """
}

process ComputeAccuracy {
  container "jdidion/python_bash"
  
  input:
  set val(name), val(err), file(alns), file(trimmed) from trimmedReads
  
  output:
  file "${name}.txt" into resultFile
  file "${name}.summary.txt" into summaryFile
  file "${name}.table.tex" into tableFile

  script:
  """
  compute_simulated_accuracy.py \
    -a1 ${alns[0]} -a2 ${alns[1]} \
    -r1 ${trimmed[0]} -r2 ${trimmed[1]} \
    --name ${name} \
    -o $resultFile -s $summaryFile -t $tableFile
  """
}
