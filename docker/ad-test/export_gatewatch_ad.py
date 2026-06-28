#!/usr/bin/env python3
import csv
import subprocess
import sys


TEST_USERS = [
    "gw.admin",
    "gw.supervisor",
    "gw.hr",
    "gw.reviewer",
    "gw.readonly",
    "gw.employee",
    "gw.disabled",
]


def samba_tool_user_show(sam):
    output = subprocess.check_output(["samba-tool", "user", "show", sam], text=True)
    fields = {}
    current_key = None
    for line in output.splitlines():
        if not line:
            continue
        if line[0].isspace() and current_key:
            fields[current_key] += line.strip()
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        current_key = key.strip()
        fields[current_key] = value.strip()
    return fields


def enabled_from_user_account_control(value):
    try:
        flags = int(value)
    except (TypeError, ValueError):
        return "TRUE"
    return "FALSE" if flags & 2 else "TRUE"


writer = csv.DictWriter(
    sys.stdout,
    fieldnames=[
        "EmployeeID",
        "Name",
        "Mail",
        "Department",
        "Office",
        "Manager",
        "Enabled",
        "ObjectGUID",
        "UserPrincipalName",
        "SamAccountName",
        "DistinguishedName",
    ],
    lineterminator="\n",
)
writer.writeheader()

for sam in TEST_USERS:
    user = samba_tool_user_show(sam)
    writer.writerow(
        {
            "EmployeeID": user.get("employeeID", ""),
            "Name": user.get("displayName") or user.get("name") or user.get("cn", ""),
            "Mail": user.get("mail", ""),
            "Department": user.get("department", ""),
            "Office": user.get("physicalDeliveryOfficeName", ""),
            "Manager": user.get("manager", ""),
            "Enabled": enabled_from_user_account_control(user.get("userAccountControl")),
            "ObjectGUID": user.get("objectGUID", ""),
            "UserPrincipalName": user.get("userPrincipalName", ""),
            "SamAccountName": user.get("sAMAccountName", sam),
            "DistinguishedName": user.get("dn", ""),
        }
    )
