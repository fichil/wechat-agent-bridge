"""Wait until the launcher assigns this helper to its private Windows Job."""
import subprocess
import sys

if __name__ == "__main__":
    if sys.stdin.readline().strip() != "start":
        sys.exit(1)
    # Descendants inherit the job; no service can escape if the launcher exits.
    sys.exit(subprocess.call(sys.argv[1:], creationflags=subprocess.CREATE_NO_WINDOW))
