#!/bin/sh
set -eu

REALM="${SAMBA_REALM:-GATEWATCH.TEST}"
DOMAIN="${SAMBA_DOMAIN:-GATEWATCH}"
ADMIN_PASSWORD="${SAMBA_ADMIN_PASSWORD:-GatewatchTest123!}"
SYNC_PASSWORD="${GATEWATCH_AD_SYNC_PASSWORD:-GatewatchSync123!}"
DNS_FORWARDER="${SAMBA_DNS_FORWARDER:-1.1.1.1}"

domain_to_base_dn() {
    printf '%s' "$1" | awk -F. '{
        for (i = 1; i <= NF; i++) {
            printf "%sDC=%s", (i == 1 ? "" : ","), tolower($i)
        }
    }'
}

write_smb_conf() {
    realm_lower="$(printf '%s' "$REALM" | tr '[:upper:]' '[:lower:]')"
    cat > /etc/samba/smb.conf <<CONF
# Global parameters
[global]
	dns forwarder = $DNS_FORWARDER
	netbios name = AD
	realm = $REALM
	server role = active directory domain controller
	workgroup = $DOMAIN
	idmap_ldb:use rfc2307 = yes

[sysvol]
	path = /var/lib/samba/sysvol
	read only = No

[netlogon]
	path = /var/lib/samba/sysvol/$realm_lower/scripts
	read only = No
CONF
}

ensure_group() {
    group_name="$1"
    description="$2"
    if samba-tool group show "$group_name" >/dev/null 2>&1; then
        return
    fi
    samba-tool group add "$group_name" --description="$description"
}

ensure_user() {
    sam="$1"
    password="$2"
    given="$3"
    surname="$4"
    mail="$5"
    employee_id="$6"
    department="$7"
    office="$8"
    enabled="$9"

    display_name="$given $surname"
    if ! samba-tool user show "$sam" >/dev/null 2>&1; then
        samba-tool user create "$sam" "$password" \
            --given-name="$given" \
            --surname="$surname" \
            --mail-address="$mail" \
            --userou="CN=Users"
    fi

    dn="$(samba-tool user show "$sam" | sed -n 's/^dn: //p' | head -n 1)"
    tmp_ldif="$(mktemp)"
    cat > "$tmp_ldif" <<LDIF
dn: $dn
changetype: modify
replace: displayName
displayName: $display_name
-
replace: employeeID
employeeID: $employee_id
-
replace: department
department: $department
-
replace: physicalDeliveryOfficeName
physicalDeliveryOfficeName: $office
-
replace: userPrincipalName
userPrincipalName: $sam@$(printf '%s' "$REALM" | tr '[:upper:]' '[:lower:]')
LDIF
    ldbmodify -H /var/lib/samba/private/sam.ldb "$tmp_ldif" >/dev/null
    rm -f "$tmp_ldif"

    if [ "$enabled" = "false" ]; then
        samba-tool user disable "$sam" >/dev/null
    else
        samba-tool user enable "$sam" >/dev/null
    fi
}

ensure_service_user() {
    sam="$1"
    password="$2"
    display_name="$3"
    mail="$4"

    if ! samba-tool user show "$sam" >/dev/null 2>&1; then
        samba-tool user create "$sam" "$password" \
            --given-name="Gatewatch" \
            --surname="AD Sync" \
            --mail-address="$mail" \
            --userou="CN=Users"
    fi

    dn="$(samba-tool user show "$sam" | sed -n 's/^dn: //p' | head -n 1)"
    tmp_ldif="$(mktemp)"
    cat > "$tmp_ldif" <<LDIF
dn: $dn
changetype: modify
replace: displayName
displayName: $display_name
-
replace: userPrincipalName
userPrincipalName: $sam@$(printf '%s' "$REALM" | tr '[:upper:]' '[:lower:]')
-
replace: description
description: Gatewatch AD sync service account for Docker test lab
LDIF
    ldbmodify -H /var/lib/samba/private/sam.ldb "$tmp_ldif" >/dev/null
    rm -f "$tmp_ldif"
    samba-tool user enable "$sam" >/dev/null
}

ensure_member() {
    group_name="$1"
    sam="$2"
    samba-tool group addmembers "$group_name" "$sam" >/dev/null 2>&1 || true
}

if [ ! -f /var/lib/samba/private/sam.ldb ]; then
    rm -f /etc/samba/smb.conf
    samba-tool domain provision \
        --server-role=dc \
        --use-rfc2307 \
        --dns-backend=SAMBA_INTERNAL \
        --realm="$REALM" \
        --domain="$DOMAIN" \
        --adminpass="$ADMIN_PASSWORD" \
        --option="dns forwarder = $DNS_FORWARDER"
fi
write_smb_conf
cp /var/lib/samba/private/krb5.conf /etc/krb5.conf || true

ensure_group "Gatewatch Admins" "Gatewatch test administrators"
ensure_group "AccessRegister-Admins" "Gatewatch production-style administrators"

ensure_user "gw.admin" "$ADMIN_PASSWORD" "Grace" "Admin" "gw.admin@gatewatch.test" "E-AD-5001" "Information Security" "HQ" "true"
ensure_user "gw.ops" "$ADMIN_PASSWORD" "Sam" "Operations" "gw.ops@gatewatch.test" "E-AD-5002" "Operations" "HQ" "true"
ensure_user "gw.people" "$ADMIN_PASSWORD" "Harper" "People" "gw.people@gatewatch.test" "E-AD-5003" "People" "HQ" "true"
ensure_user "gw.compliance" "$ADMIN_PASSWORD" "Riley" "Compliance" "gw.compliance@gatewatch.test" "E-AD-5004" "Compliance" "HQ" "true"
ensure_user "gw.audit" "$ADMIN_PASSWORD" "Remy" "Audit" "gw.audit@gatewatch.test" "E-AD-5005" "Audit" "Remote" "true"
ensure_user "gw.employee" "$ADMIN_PASSWORD" "Evan" "Employee" "gw.employee@gatewatch.test" "E-AD-5006" "Finance" "Branch" "true"
ensure_user "gw.disabled" "$ADMIN_PASSWORD" "Dana" "Disabled" "gw.disabled@gatewatch.test" "E-AD-5007" "Finance" "Branch" "false"
ensure_service_user "svc.gatewatch.adsync" "$SYNC_PASSWORD" "Gatewatch AD Sync Service" "svc.gatewatch.adsync@gatewatch.test"

ensure_member "Gatewatch Admins" "gw.admin"
ensure_member "AccessRegister-Admins" "gw.admin"
ensure_member "AccessRegister-Admins" "svc.gatewatch.adsync"

exec samba -i -M single
