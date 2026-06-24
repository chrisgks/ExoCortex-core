# Bootstrap Tools

This folder contains first-run and scaffold helpers for ExoCortex.

Current entrypoint:

- `init.py`: initialize a clean clone for use, or scaffold new domains and projects from templates

Typical usage:

```bash
python3 tools/bootstrap/init.py --install-wrappers --install-cron
python3 tools/bootstrap/init.py domain research
python3 tools/bootstrap/init.py project work my-project
```
