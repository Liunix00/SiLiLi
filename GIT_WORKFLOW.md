# Git Workflow

## One-time Setup

```bash
git remote -v
git push -u origin main
```

If push fails with credential errors, authenticate first:

```bash
gh auth login
```

## Daily Development

1. Sync main branch:

```bash
git checkout main
git pull origin main
```

2. Create feature branch:

```bash
git checkout -b feat/short-description
```

3. Commit frequently:

```bash
git add .
git commit -m "feat: short summary"
```

4. Push branch and open PR:

```bash
git push -u origin feat/short-description
```

5. After PR merged:

```bash
git checkout main
git pull origin main
git branch -d feat/short-description
```

## Branch Naming

- `feat/*`: new features
- `fix/*`: bug fixes
- `chore/*`: maintenance
- `docs/*`: documentation
- `refactor/*`: code refactoring

## Commit Message Format

- `feat: add xxx`
- `fix: resolve xxx`
- `chore: update xxx`

## Team Safety Rules

- Do not develop directly on `main`.
- Keep PRs small and focused.
- Rebase or merge `main` regularly to reduce conflicts.
- Avoid using `git add .` when unrelated temporary files exist.

## Suggested GitHub Settings

- Protect `main` branch.
- Require pull request review before merge.
- Optionally require CI status checks before merge.
