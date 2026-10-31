# Git workflow

## Branches
| Branch | Purpose |
|---|---|
| `main` | Always runnable. Only receives merges from `dev` once a milestone works. |
| `dev` | Integration branch. Day-to-day work lands here. |
| `feat/<module>` | One branch per module (e.g. `feat/accounts`), merged into `dev` with `--no-ff`. |

## Commit messages (Conventional Commits)
`<type>(<scope>): <summary>` e.g. `feat(accounts): add custom user with unique email`

Types: `feat`, `fix`, `refactor`, `perf`, `test`, `docs`, `chore`, `style`.
One logical change per commit. Migrations go in the same commit as the model change.

## Per-step routine
```
git checkout dev
git checkout -b feat/<module>
# ... work, then:
git add -A
git status            # confirm no .env, venv or zip files are staged
git commit            # opens the template in .gitmessage
git checkout dev
git merge --no-ff feat/<module>
git push origin dev
```

## Release to main
```
git checkout main
git merge --no-ff dev
git tag -a v0.1.0 -m "Milestone: <name>"
git push origin main --tags
```

## Rules
- Never commit `.env`. Only `.env.example` is tracked.
- Never rewrite history that has been pushed.
- Run `python manage.py check` before every commit.
