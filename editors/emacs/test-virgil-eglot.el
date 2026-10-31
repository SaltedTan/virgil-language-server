;;; test-virgil-eglot.el --- ERT tests for virgil-eglot.el -*- lexical-binding: t; -*-

;; Copyright 2026 The Virgil Language Server Authors.
;; SPDX-License-Identifier: Apache-2.0

;;; Commentary:

;; Run from the repository root:
;;
;;   emacs -Q --batch -L editors/emacs -l editors/emacs/test-virgil-eglot.el \
;;     -f ert-run-tests-batch-and-exit
;;
;; The last test starts `virgil-lsp' through Eglot, and is skipped when
;; it isn't on `exec-path'.  Tests that need a directory outside version
;; control are skipped when the temporary directory is inside one, and
;; the Git test is skipped without `git'.

;;; Code:

(require 'ert)
(require 'cl-lib)

;; What loading the library changes is recorded before and after it.
(defvar test-virgil-eglot--processes-before (process-list))
(defvar test-virgil-eglot--hooks
  '(prog-mode-hook find-file-hook after-change-major-mode-hook
    change-major-mode-hook))
(defvar test-virgil-eglot--hooks-before
  (mapcar (lambda (h) (and (boundp h) (copy-sequence (symbol-value h))))
          test-virgil-eglot--hooks))
(defvar test-virgil-eglot--eglot-before (featurep 'eglot))

(require 'virgil-eglot)

(defvar test-virgil-eglot--processes-after (process-list))
(defvar test-virgil-eglot--eglot-after (featurep 'eglot))
(defvar test-virgil-eglot--hooks-after
  (mapcar (lambda (h) (and (boundp h) (copy-sequence (symbol-value h))))
          test-virgil-eglot--hooks))

(require 'project)
(require 'eglot)

(defvar test-virgil-eglot--entry
  '((virgil-mode :language-id "virgil") "virgil-lsp" "--stdio"))

(defmacro test-virgil-eglot--with-dir (var &rest body)
  "Bind VAR to a new temporary directory, run BODY, then remove it.
Buffers visiting files in it are killed first."
  (declare (indent 1))
  `(let ((,var (file-name-as-directory
                (file-truename (make-temp-file "test-virgil-eglot" t)))))
     (unwind-protect (progn ,@body)
       (dolist (b (buffer-list))
         (let ((f (buffer-file-name b)))
           (when (and f (string-prefix-p ,var f))
             (with-current-buffer b (set-buffer-modified-p nil))
             (kill-buffer b))))
       (delete-directory ,var t))))

(defun test-virgil-eglot--write (dir name &optional text)
  "Write TEXT (or nothing) to NAME under DIR, creating directories."
  (let ((file (expand-file-name name dir)))
    (make-directory (file-name-directory file) t)
    (write-region (or text "") nil file nil 'silent)
    file))

(defun test-virgil-eglot--root (dir name)
  "Visit NAME under DIR and return its project root, or nil."
  (with-current-buffer (find-file-noselect (expand-file-name name dir))
    (let ((project (project-current)))
      (and project (file-name-as-directory (project-root project))))))

(defun test-virgil-eglot--skip-unless-outside-vc (dir)
  "Skip the test when DIR is inside a version-controlled checkout."
  (when (project-try-vc dir)
    (ert-skip (format "%s is under version control" dir))))

;;; Loading.

(ert-deftest test-virgil-eglot-load-starts-nothing ()
  "Loading starts no process, doesn't load Eglot, and adds no global hooks."
  (should (equal test-virgil-eglot--processes-after
                 test-virgil-eglot--processes-before))
  (should-not test-virgil-eglot--eglot-before)
  (should-not test-virgil-eglot--eglot-after)
  (should (equal test-virgil-eglot--hooks-after
                 test-virgil-eglot--hooks-before))
  (should-not (memq #'eglot-ensure (bound-and-true-p virgil-mode-hook))))

(ert-deftest test-virgil-eglot-visit-starts-nothing ()
  "Visiting a `.v3' file in a configured project starts no process."
  (test-virgil-eglot--with-dir dir
    (test-virgil-eglot--write dir ".virgil-lsp.json" "{}")
    (let ((before (process-list)))
      (with-current-buffer
          (find-file-noselect (test-virgil-eglot--write dir "src/A.v3"))
        (should (eq major-mode 'virgil-mode))
        (should-not (bound-and-true-p eglot--managed-mode)))
      (should (equal (process-list) before)))))

(ert-deftest test-virgil-eglot-load-twice ()
  "Loading the file again adds no second entry or hook function."
  (load "virgil-eglot" nil t)
  (should (= 1 (cl-count test-virgil-eglot--entry eglot-server-programs
                         :test #'equal)))
  (should (= 1 (cl-count #'virgil-eglot-find-project project-find-functions)))
  (should (= 1 (cl-count '("\\.v3\\'" . virgil-mode) auto-mode-alist
                         :test #'equal))))

;;; The mode.

(ert-deftest test-virgil-eglot-v3-detection ()
  "`.v3' files open in `virgil-mode', which derives from `prog-mode'."
  (test-virgil-eglot--with-dir dir
    (with-current-buffer
        (find-file-noselect (test-virgil-eglot--write dir "Words.v3"))
      (should (eq major-mode 'virgil-mode))
      (should (derived-mode-p 'prog-mode)))
    (with-current-buffer
        (find-file-noselect (test-virgil-eglot--write dir "Words.v3x"))
      (should-not (eq major-mode 'virgil-mode)))))

(ert-deftest test-virgil-eglot-comment-syntax ()
  "`//' and `/* */' are comments, and `comment-region' uses `//'."
  (with-temp-buffer
    (virgil-mode)
    (insert "var x = 1; // note\n/* block\n */ var y = 2;")
    (goto-char (point-min))
    (search-forward "no")
    (should (nth 4 (syntax-ppss)))
    (search-forward "blo")
    (should (nth 4 (syntax-ppss)))
    (search-forward "var y")
    (should-not (nth 4 (syntax-ppss))))
  (with-temp-buffer
    (virgil-mode)
    (insert "var x = 1;")
    (comment-region (point-min) (point-max))
    (should (equal (buffer-string) "// var x = 1;"))))

;;; The Eglot entry.

(ert-deftest test-virgil-eglot-language-id ()
  "The entry gives the language ID explicitly, only for `virgil-mode'."
  (should (member test-virgil-eglot--entry eglot-server-programs))
  ;; No other entry starts this server.
  (should (equal (cl-remove-if-not
                  (lambda (e) (equal (cdr e) '("virgil-lsp" "--stdio")))
                  eglot-server-programs)
                 (list test-virgil-eglot--entry)))
  (test-virgil-eglot--with-dir dir
    (test-virgil-eglot--write dir ".virgil-lsp.json" "{}")
    (with-current-buffer
        (find-file-noselect (test-virgil-eglot--write dir "A.v3"))
      (pcase-let ((`(,modes ,_project ,_class ,contact ,language-ids)
                   (eglot--guess-contact)))
        (should (equal modes '(virgil-mode)))
        (should (equal contact '("virgil-lsp" "--stdio")))
        (should (equal language-ids '("virgil")))))))

(ert-deftest test-virgil-eglot-unrelated-buffers ()
  "Other buffers get neither the mode, the server, nor the project root."
  (dolist (mode '(prog-mode c-mode js-json-mode fundamental-mode))
    (should-not (equal (cdr (eglot--lookup-mode mode))
                       '("virgil-lsp" "--stdio"))))
  (test-virgil-eglot--with-dir dir
    (test-virgil-eglot--skip-unless-outside-vc dir)
    (test-virgil-eglot--write dir ".virgil-lsp.json" "{}")
    (dolist (name '(".virgil-lsp.json" "main.c" "data.json" "notes.txt"))
      (with-current-buffer
          (find-file-noselect (test-virgil-eglot--write dir name "x"))
        (should-not (eq major-mode 'virgil-mode))
        (should-not (bound-and-true-p eglot--managed-mode))
        (should-not (virgil-eglot-find-project dir))
        (should-not (project-current))))))

;;; Project roots.

(ert-deftest test-virgil-eglot-root-outside-vc ()
  "Outside version control, the root is the nearest `.virgil-lsp.json'."
  (test-virgil-eglot--with-dir dir
    (test-virgil-eglot--skip-unless-outside-vc dir)
    (test-virgil-eglot--write dir ".virgil-lsp.json" "{}")
    (test-virgil-eglot--write dir "src/deep/A.v3")
    (should (equal (test-virgil-eglot--root dir "src/deep/A.v3") dir))))

(ert-deftest test-virgil-eglot-root-nested ()
  "A nested `.virgil-lsp.json' roots the files below it."
  (test-virgil-eglot--with-dir dir
    (test-virgil-eglot--skip-unless-outside-vc dir)
    (test-virgil-eglot--write dir ".virgil-lsp.json" "{}")
    (test-virgil-eglot--write dir "inner/.virgil-lsp.json" "{}")
    (test-virgil-eglot--write dir "Outer.v3")
    (test-virgil-eglot--write dir "inner/src/Inner.v3")
    (should (equal (test-virgil-eglot--root dir "Outer.v3") dir))
    (should (equal (test-virgil-eglot--root dir "inner/src/Inner.v3")
                   (expand-file-name "inner/" dir)))))

(ert-deftest test-virgil-eglot-root-without-project-file ()
  "Without a project file or version control, there is no project."
  (test-virgil-eglot--with-dir dir
    (test-virgil-eglot--skip-unless-outside-vc dir)
    (test-virgil-eglot--write dir "A.v3")
    (should-not (test-virgil-eglot--root dir "A.v3"))))

(ert-deftest test-virgil-eglot-root-in-git ()
  "In a Git checkout, the checkout's root wins over a nested project file."
  (skip-unless (executable-find "git"))
  (test-virgil-eglot--with-dir dir
    (let ((default-directory dir))
      (should (zerop (call-process "git" nil nil nil "init" "--quiet"))))
    (test-virgil-eglot--write dir "sub/.virgil-lsp.json" "{}")
    (test-virgil-eglot--write dir "sub/src/A.v3")
    (should (equal (test-virgil-eglot--root dir "sub/src/A.v3") dir))
    (should (eq 'vc (car (with-current-buffer
                             (find-file-noselect
                              (expand-file-name "sub/src/A.v3" dir))
                           (project-current)))))))

;;; End to end.

(ert-deftest test-virgil-eglot-server ()
  "Eglot starts `virgil-lsp', gets a diagnostic, and shuts it down."
  (skip-unless (executable-find "virgil-lsp"))
  (test-virgil-eglot--with-dir dir
    (test-virgil-eglot--write
     dir ".virgil-lsp.json"
     "{\"version\": 1, \"projects\": [{\"name\": \"t\", \"sources\": [\"*.v3\"]}]}\n")
    (with-current-buffer
        (find-file-noselect
         (test-virgil-eglot--write
          dir "Bad.v3" "component Bad {\n\tdef f() -> int { return \"s\"; }\n}\n"))
      (apply #'eglot (eglot--guess-contact))
      (let ((server (eglot-current-server))
            (deadline (+ (float-time) 60)))
        (unwind-protect
            (progn
              (should (equal (eglot--language-ids server) '("virgil")))
              ;; Flymake asks for a report only from an idle timer, which
              ;; doesn't run in batch mode; until then, Eglot keeps the
              ;; published diagnostics in `eglot--diagnostics'.
              (while (and (null eglot--diagnostics)
                          (< (float-time) deadline))
                (accept-process-output nil 0.05))
              (should (cl-some (lambda (d)
                                 (string-match-p "TypeError"
                                                 (flymake-diagnostic-text d)))
                               eglot--diagnostics)))
          (eglot-shutdown server))
        (should-not (jsonrpc-running-p server))))))

(provide 'test-virgil-eglot)

;;; test-virgil-eglot.el ends here
