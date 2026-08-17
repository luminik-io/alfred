import json
import os
import pathlib
import subprocess
import sys

repo = pathlib.Path(sys.argv[1])
source = repo / "limits.py"
original = source.read_text() if source.exists() else ""
expected = (pathlib.Path(__file__).parent / "seed" / "limits.py").read_text()


def run_tests():
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-v"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    ).returncode


findings = []
original_green = run_tests() == 0
mutants = [
    expected.replace("return 10", "return 11"),
    expected.replace("value > 100", "value > 101"),
    expected.replace("isinstance(value, bool) or not isinstance(value, int)", "False"),
]
killed = 0
try:
    for mutant in mutants:
        source.write_text(mutant)
        if run_tests() != 0:
            killed += 1
finally:
    source.write_text(original)
if killed < len(mutants):
    findings.append(f"The tests killed {killed} of {len(mutants)} required mutants.")
print(
    json.dumps(
        {
            "task_passed": original_green and killed == len(mutants),
            "regression": original != expected or not original_green,
            "review_findings": findings,
        }
    )
)
