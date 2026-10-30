;;; virgil-eglot.el --- Minimal Virgil mode and Eglot entry -*- lexical-binding: t; -*-

;; Copyright 2026 The Virgil Language Server Authors.
;; SPDX-License-Identifier: Apache-2.0

;;; Commentary:

;; A minimal Emacs setup for the Virgil Language Server
;; (https://github.com/SaltedTan/virgil-language-server):
;;
;; - `virgil-mode', derived from `prog-mode', for `.v3' files.  It sets
;;   up `//' and `/* */' comments and nothing else: no fontification and
;;   no indentation rules.
;; - An `eglot-server-programs' entry that starts `virgil-lsp --stdio'
;;   for `virgil-mode' buffers with the language ID "virgil".
;; - A `project-find-functions' entry, for `virgil-mode' buffers outside
;;   any version-controlled checkout, that roots the project at the
;;   nearest directory containing `.virgil-lsp.json'.
;;
;; Loading this file starts no process and doesn't load or enable Eglot.
;; Start the server with M-x eglot in a Virgil buffer, or opt in to
;; starting it whenever a Virgil file is visited:
;;
;;   (add-hook 'virgil-mode-hook #'eglot-ensure)
;;
;; See editors/emacs/README.md for installation and the Virgil root.

;;; Code:

(defvar eglot-server-programs)
(defvar project-find-functions)

(defvar virgil-mode-syntax-table
  (let ((table (make-syntax-table)))
    ;; `//' line comments and `/* */' block comments.
    (modify-syntax-entry ?/ ". 124b" table)
    (modify-syntax-entry ?* ". 23" table)
    (modify-syntax-entry ?\n "> b" table)
    table)
  "Syntax table for `virgil-mode'.")

;;;###autoload
(define-derived-mode virgil-mode prog-mode "Virgil"
  "Major mode for Virgil source files.
It provides comment syntax, so that the comment commands work, and
nothing else: no syntax fontification or indentation.  Run \\[eglot]
to start the Virgil Language Server for the buffer's project."
  (setq-local comment-start "// ")
  (setq-local comment-end "")
  (setq-local comment-start-skip "\\(?://+\\|/\\*+\\)\\s *"))

;;;###autoload
(add-to-list 'auto-mode-alist '("\\.v3\\'" . virgil-mode))

;; Only `virgil-mode' buffers are managed, with an explicit language ID
;; rather than one guessed from the mode's name.  No initialization
;; options are sent, so the server uses the library bundled beside it.
(with-eval-after-load 'eglot
  (add-to-list 'eglot-server-programs
               '((virgil-mode :language-id "virgil") "virgil-lsp" "--stdio")))

(defun virgil-eglot-find-project (dir)
  "Return a project rooted at the nearest `.virgil-lsp.json' above DIR.
Return nil unless the current buffer is in `virgil-mode' and DIR or
one of its parents contains `.virgil-lsp.json'.  This only checks
whether the file exists: it doesn't read it or run anything."
  (when (derived-mode-p 'virgil-mode)
    (let ((root (locate-dominating-file dir ".virgil-lsp.json")))
      (when root
        (cons 'transient (expand-file-name root))))))

;; Appended, so the version-control finder runs first: a checkout keeps
;; its usual root, and this applies only to Virgil files outside one.
(with-eval-after-load 'project
  (add-hook 'project-find-functions #'virgil-eglot-find-project 90))

(provide 'virgil-eglot)

;;; virgil-eglot.el ends here
