import logging

# Several tests deliberately exercise Vault/GitLab/NetBox/vCenter "unreachable" fallback
# paths, which log a warning via logger.warning(..., exc_info=True) — expected noise, not
# a real failure. Silenced here so `manage.py test` output stays readable.
logging.disable(logging.CRITICAL)
