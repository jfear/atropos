#!/bin/bash
# Usage: docker2singularity.sh <docker2singularity container name> <remote host> <remote host dir>
D2S=$1
REMOTE_HOST=$2
REMOTE_DIR=$3
while read -r container
do
   echo "Transferring $container to ${REMOTE_HOST}:${REMOTE_DIR}" \
&& name=`basename $container` \
&& file="${name}.img" \
&& docker run \
   -v /var/run/docker.sock:/var/run/docker.sock \
   -v $(pwd):/output --privileged -t --rm $D2S $container \
&& mv *.img $file \
&& scp $file ${REMOTE_HOST}:${REMOTE_DIR} \
&& rm -Rf $file
done < <(grep -v -P "^#" ../../containers/paper_containers.txt)
