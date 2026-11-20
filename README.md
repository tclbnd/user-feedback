# netaddr

inet and cidr support for the security-group rule engine. Rules are kept
in PostgreSQL inet/cidr columns by the control plane and evaluated by this
engine on the gateways; audits compare both sides' output, so input
parsing, text output, every function and every operator here must match
PostgreSQL exactly, IPv4 and IPv6, including the cases PostgreSQL rejects.

## Layout

- `netaddr/types.py` – inet/cidr values, input and output
- `netaddr/functions.py` – functions and operators
- `netcall.py` – evaluates a list of calls

## Running

    python3 netcall.py examples/rules.json out.json
    python3 -m unittest discover -s tests
