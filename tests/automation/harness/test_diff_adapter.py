"""Unit tests for the diff-adapter helpers.

Phase 2a scope: only the file-count and file-path extraction helpers.
Phase 2b will add a real unified-diff -> FilePatch parser; tests for that
go in a parallel ``test_file_patch_parser.py``.
"""

from seer.automation.harness.diff_adapter import count_files_in_diff, file_paths_in_diff

_TWO_FILE_DIFF = """\
diff --git a/src/foo.py b/src/foo.py
index abc..def 100644
--- a/src/foo.py
+++ b/src/foo.py
@@ -1,3 +1,4 @@
 def foo():
-    return 1
+    return 2
+    # bumped
diff --git a/tests/test_foo.py b/tests/test_foo.py
index 111..222 100644
--- a/tests/test_foo.py
+++ b/tests/test_foo.py
@@ -1,1 +1,1 @@
-assert foo() == 1
+assert foo() == 2
"""

_EMPTY_DIFF = ""

_CREATE_AND_DELETE_DIFF = """\
diff --git a/new_file.py b/new_file.py
new file mode 100644
index 0000000..abc
--- /dev/null
+++ b/new_file.py
@@ -0,0 +1,3 @@
+def added():
+    pass
+
diff --git a/old_file.py b/old_file.py
deleted file mode 100644
index abc..0000000
--- a/old_file.py
+++ /dev/null
@@ -1,2 +0,0 @@
-def removed():
-    pass
"""


def test_count_files_in_two_file_diff():
    assert count_files_in_diff(_TWO_FILE_DIFF) == 2


def test_count_files_in_empty_diff():
    assert count_files_in_diff(_EMPTY_DIFF) == 0


def test_count_files_in_create_and_delete():
    assert count_files_in_diff(_CREATE_AND_DELETE_DIFF) == 2


def test_file_paths_in_two_file_diff():
    paths = list(file_paths_in_diff(_TWO_FILE_DIFF))
    assert paths == ["src/foo.py", "tests/test_foo.py"]


def test_file_paths_in_empty_diff():
    assert list(file_paths_in_diff(_EMPTY_DIFF)) == []


def test_file_paths_in_create_and_delete_diff():
    paths = list(file_paths_in_diff(_CREATE_AND_DELETE_DIFF))
    assert paths == ["new_file.py", "old_file.py"]
