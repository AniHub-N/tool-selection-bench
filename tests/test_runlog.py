import pytest

from bench.runlog import ResumeConflict, RunDir


def test_resume_reuses_unfinished_run(tmp_path):
    r1 = RunDir.start("flash", "free", runs_dir=tmp_path)
    r1.log("calls", {"task_id": "t1", "rep": 0})
    r1.set_status("paused", reason="quota")
    r2 = RunDir.start("flash", "free", runs_dir=tmp_path)
    assert r2.path == r1.path
    assert r2.done("calls", lambda r: (r["task_id"], r["rep"])) == {("t1", 0)}
    assert len(r2.meta["sessions"]) == 2


def test_complete_run_is_not_resumed(tmp_path):
    r1 = RunDir.start("flash", "free", runs_dir=tmp_path)
    r1.set_status("complete")
    r2 = RunDir.start("flash", "free", runs_dir=tmp_path)
    assert r2.path != r1.path


def test_new_flag_skips_resume(tmp_path):
    r1 = RunDir.start("flash", "free", runs_dir=tmp_path)
    r2 = RunDir.start("flash", "free", runs_dir=tmp_path, resume=False)
    assert r1.path != r2.path


def test_changed_config_blocks_resume(tmp_path):
    r1 = RunDir.start("flash", "free", runs_dir=tmp_path)
    meta = r1.meta
    meta["experiment_hash"] = "different"
    r1._write_meta(meta)
    with pytest.raises(ResumeConflict):
        RunDir.start("flash", "free", runs_dir=tmp_path)
