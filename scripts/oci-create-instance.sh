#!/bin/bash
# Auto-retry Oracle Cloud ARM instance creation until capacity is available
while true; do
    echo "$(date) - Attempting to create instance..."
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
        echo "SUCCESS! Instance created:"
        echo "$result"
        break
    else
        echo "$(date) - Failed (likely out of capacity). Retrying in 60s..."
        echo "$result" | grep '"message"'
    fi
    sleep 60
done
