import brainbench_providers


def test_package_exposes_version_and_task_classes() -> None:
    assert brainbench_providers.__version__ == "0.1.0"
    assert "tutoring" in brainbench_providers.TASK_CLASSES
    assert len(set(brainbench_providers.TASK_CLASSES)) == 6
