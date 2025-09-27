MOUNT=kmh-nfs-ssd-us-mount
# MOUNT=kmh-nfs-us-mount
# MOUNT=kmh-nfs-ssd-eu-mount
CMD="python prepare_dataset.py --config configs/default.py --imagenet_root /$MOUNT/data/imagenet --output_dir /$MOUNT/data/vae_cached_muvar_imagenet_zhh --overwrite --flip"
VM_NAME=kmh-tpuvm-v4-32-spot-yiyang3
# VM_NAME=kmh-tpuvm-v5e-32-spot-2
ZONE=us-central2-b
# ZONE=us-central1-a

HERE=/kmh-nfs-ssd-us-mount/staging/sqa/sbsbsbsbsbsbssb/meanflow
sudo rsync -av . $HERE --exclude='.git'
sudo chmod 777 -R $HERE

MOUNT_DISK="
sudo mkdir -p /kmh-nfs-ssd-us-mount
sudo mount -o vers=3 10.97.81.98:/kmh_nfs_ssd_us /kmh-nfs-ssd-us-mount
sudo chmod go+rw /kmh-nfs-ssd-us-mount
"

gcloud compute tpus tpu-vm ssh $VM_NAME --zone $ZONE --command "echo start at \$(date) && sudo apt update -y && sudo apt install nfs-common -y
$MOUNT_DISK 
ls /$MOUNT && echo done at \$(date)" --worker=all

gcloud compute tpus tpu-vm ssh $VM_NAME --zone $ZONE --command "ls /kmh-nfs-ssd-us-mount && echo Starting at \$(date); source /etc/profile; cd $HERE; $CMD; echo Finished at \$(date)" --worker=all

# gcloud compute tpus tpu-vm ssh $VM_NAME --zone $ZONE --command "ps -ef | grep prepare_dataset.py | grep -v grep | awk '{print \$2}'; echo Killed at \$(date)" --worker=all
