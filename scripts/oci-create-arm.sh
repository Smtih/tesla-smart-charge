#!/bin/bash
# Auto-retry ARM A1 instance
while true; do
    echo "$(date) [ARM] Attempting..."
    result=$(oci compute instance launch \
        --compartment-id "ocid1.tenancy.oc1..aaaaaaaa4w24kbdlfhu7czwdhyf5kr4tjvzk3zt6krwtx5x7hgfhttoyxosq" \
        --availability-domain "YbPA:AP-MELBOURNE-1-AD-1" \
        --shape "VM.Standard.A1.Flex" \
        --shape-config '{"ocpus":1,"memoryInGBs":6}' \
        --image-id "ocid1.image.oc1.ap-melbourne-1.aaaaaaaameaiob3abo7nzzg4hb2kmwi5ihqmkpbwt2hax65szewv52rt3z6a" \
        --subnet-id "ocid1.subnet.oc1.ap-melbourne-1.aaaaaaaauz6nhxmr42edyxodmknibdvym7x7otsj6ahe3j6rocquhu6ztqua" \
        --assign-public-ip true \
        --display-name "tesla-telemetry" \
        --ssh-authorized-keys-file "C:/Users/smith/.oci/tesla-vm-key.pub" 2>&1)
    if echo "$result" | grep -q '"lifecycle-state"'; then
        echo "ARM SUCCESS!"
        echo "$result"
        break
    fi
    echo "$(date) [ARM] Failed. Retrying in 90s..."
    sleep 90
done
