#!/bin/bash
# Auto-retry AMD Micro instance
while true; do
    echo "$(date) [AMD] Attempting..."
    result=$(oci compute instance launch \
        --compartment-id "ocid1.tenancy.oc1..aaaaaaaa4w24kbdlfhu7czwdhyf5kr4tjvzk3zt6krwtx5x7hgfhttoyxosq" \
        --availability-domain "YbPA:AP-MELBOURNE-1-AD-1" \
        --shape "VM.Standard.E2.1.Micro" \
        --image-id "ocid1.image.oc1.ap-melbourne-1.aaaaaaaaqqdeha26uiruexkxy6fcpgjc5ufw3t5sj4oy3xmkgil7p3ielzwa" \
        --subnet-id "ocid1.subnet.oc1.ap-melbourne-1.aaaaaaaauz6nhxmr42edyxodmknibdvym7x7otsj6ahe3j6rocquhu6ztqua" \
        --assign-public-ip true \
        --display-name "tesla-telemetry-amd" \
        --ssh-authorized-keys-file "C:/Users/smith/.oci/tesla-vm-key.pub" 2>&1)
    if echo "$result" | grep -q '"lifecycle-state"'; then
        echo "AMD SUCCESS!"
        echo "$result"
        break
    fi
    echo "$(date) [AMD] Failed. Retrying in 90s..."
    sleep 90
done
