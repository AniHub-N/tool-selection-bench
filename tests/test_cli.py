"""The CLI module must import and parse every subcommand (catches syntax errors in cli.py)."""

import pytest

from bench import cli


@pytest.mark.parametrize("argv", [["smoke", "--help"], ["tasks-check", "--help"], ["run", "--help"],
                                  ["memorized", "--help"]])
def test_subcommands_parse(argv):
    with pytest.raises(SystemExit) as e:
        cli.main(argv)
    assert e.value.code == 0
