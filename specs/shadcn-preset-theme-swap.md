# Plan: Swap shadcn Theme to Preset b3lV0H9rs

## Task Description
Replace the current shadcn `base-nova` style preset with the community preset `b3lV0H9rs` using `pnpm dlx shadcn@latest init --preset b3lV0H9rs --template next`. The existing UI layout and components must be preserved — only the design system tokens (colors, radii, fonts, component styles) should change.

## Objective
Apply the new shadcn preset theme across the entire frontend while preserving all existing page structure, custom components, business logic, and custom CSS variables (`--available`, `--connection`).

## Problem Statement
The current frontend uses the `base-nova` shadcn style with a custom dark-only orange-primary theme. The user wants to swap to a different shadcn preset (`b3lV0H9rs`) which will change the design system foundation — CSS variables, component styles, and potentially the underlying component primitive library. This must be done without breaking the existing UI functionality.

## Solution Approach
1. Run the shadcn init command with the new preset, allowing it to overwrite `components.json` and `globals.css`
2. Restore custom CSS variables (`--available`, `--connection`) that the preset will remove
3. Re-add all 5 existing shadcn UI components (`button`, `input`, `table`, `badge`, `dialog`) via `pnpm dlx shadcn@latest add` to get versions matching the new style
4. Audit and update the 4 custom components for any class name or API changes
5. Verify the app builds and renders correctly

## Relevant Files

### Overwritten by preset (backed up first)
- `web/frontend/components.json` — shadcn configuration; will be replaced by preset
- `web/frontend/app/globals.css` — CSS variables and theme; will be replaced by preset

### UI components (re-generated after preset)
- `web/frontend/components/ui/button.tsx` — Button with CVA variants; will be re-added via shadcn CLI
- `web/frontend/components/ui/input.tsx` — Input primitive; will be re-added
- `web/frontend/components/ui/table.tsx` — Table components; will be re-added
- `web/frontend/components/ui/badge.tsx` — Badge with CVA variants; will be re-added
- `web/frontend/components/ui/dialog.tsx` — Dialog modal; will be re-added

### Custom components (audit for breakage)
- `web/frontend/components/search-form.tsx` — Uses `Input`, `Button` (variants: ghost, icon size)
- `web/frontend/components/results-table.tsx` — Uses `Table*`, `AvailabilityBadge`, `Button` (variant: ghost, size: icon-sm)
- `web/frontend/components/detail-modal.tsx` — Uses `Dialog*`, `Table*`
- `web/frontend/components/availability-badge.tsx` — Uses hardcoded Tailwind classes (not shadcn Badge), references `--available`/`--connection` indirectly

### Supporting files (may need updates)
- `web/frontend/lib/utils.ts` — `cn()` helper; should survive as-is
- `web/frontend/app/layout.tsx` — Root layout with font variables and dark class
- `web/frontend/app/page.tsx` — Home page with card styling
- `web/frontend/app/search/page.tsx` — Search results page
- `web/frontend/package.json` — Dependencies; preset may add/change deps (e.g., swap `@base-ui/react` for `radix-ui`)

### New Files
- None expected (all files already exist)

## Implementation Phases

### Phase 1: Foundation — Run Preset & Restore Custom Tokens
- Back up current `globals.css` and `components.json` (git tracks them, but save copies for reference)
- Run `pnpm dlx shadcn@latest init --preset b3lV0H9rs --template next` from `web/frontend/`
- Inspect the new `globals.css` and `components.json` to understand what changed
- Re-add custom CSS variables `--available` and `--connection` to both `:root` and `.dark` blocks
- Install any new dependencies the preset added (run `npm install` or `pnpm install`)

### Phase 2: Core Implementation — Regenerate UI Components
- Re-add all 5 shadcn components matching the new style: `pnpm dlx shadcn@latest add button input table badge dialog`
- Compare new component APIs against what custom components expect (variant names, size names, prop types)
- Fix any breaking changes in custom components (e.g., if `icon-sm` size no longer exists, map to equivalent)

### Phase 3: Integration & Polish
- Run `npm run build` (or `pnpm build`) to catch type errors and build failures
- Visually verify home page, search results page, and detail modal
- Ensure dark mode still applies correctly
- Confirm custom availability badge colors still work

## Team Orchestration

- You operate as the team lead and orchestrate the team to execute the plan.
- You're responsible for deploying the right team members with the right context to execute the plan.
- IMPORTANT: You NEVER operate directly on the codebase. You use `Task` and `Task*` tools to deploy team members to do the building, validating, testing, deploying, and other tasks.
  - This is critical. Your job is to act as a high level director of the team, not a builder.
  - Your role is to validate all work is going well and make sure the team is on track to complete the plan.
  - You'll orchestrate this by using the Task* Tools to manage coordination between the team members.
  - Communication is paramount. You'll use the Task* Tools to communicate with the team members and ensure they're on track to complete the plan.
- Take note of the session id of each team member. This is how you'll reference them.

### Team Members

- Builder
  - Name: theme-swapper
  - Role: Run the shadcn preset init command, handle dependency changes, and restore custom CSS variables
  - Agent Type: frontend-architect
  - Resume: true

- Builder
  - Name: component-updater
  - Role: Re-generate shadcn UI components and fix any API/class breakage in custom components
  - Agent Type: frontend-architect
  - Resume: true

- Builder
  - Name: validator
  - Role: Validate the build succeeds, no type errors, and UI renders correctly
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

- IMPORTANT: Execute every step in order, top to bottom. Each task maps directly to a `TaskCreate` call.
- Before you start, run `TaskCreate` to create the initial task list that all team members can see and execute.

### 1. Back Up Current Theme Files
- **Task ID**: backup-theme
- **Depends On**: none
- **Assigned To**: theme-swapper
- **Agent Type**: frontend-architect
- **Parallel**: false
- Copy `web/frontend/app/globals.css` to `web/frontend/app/globals.css.bak`
- Copy `web/frontend/components.json` to `web/frontend/components.json.bak`
- Record the current custom CSS variables that must be preserved:
  - `--available: hsl(142.1 76.2% 36.3%);`
  - `--connection: hsl(24.6 95% 53.1%);`

### 2. Run shadcn Preset Init
- **Task ID**: run-preset-init
- **Depends On**: backup-theme
- **Assigned To**: theme-swapper
- **Agent Type**: frontend-architect
- **Parallel**: false
- `cd web/frontend && pnpm dlx shadcn@latest init --preset b3lV0H9rs --template next`
- Accept overwrites when prompted (use `--yes` or `--overwrite` flag if available)
- After completion, inspect the new `components.json` to identify style changes (new style name, base color, primitive library)
- Inspect the new `globals.css` to see new CSS variable values

### 3. Restore Custom CSS Variables
- **Task ID**: restore-custom-vars
- **Depends On**: run-preset-init
- **Assigned To**: theme-swapper
- **Agent Type**: frontend-architect
- **Parallel**: false
- Add `--available: hsl(142.1 76.2% 36.3%);` and `--connection: hsl(24.6 95% 53.1%);` back into both `:root` and `.dark` blocks in `globals.css`
- Add `--color-available: var(--available);` and `--color-connection: var(--connection);` to the `@theme inline` block
- Preserve the `--font-sans`, `--font-mono`, `--font-heading` mappings if the preset removed them (needed by layout.tsx font setup)
- Ensure `@custom-variant dark (&:is(.dark *));` is present if needed for dark mode

### 4. Install Dependencies
- **Task ID**: install-deps
- **Depends On**: run-preset-init
- **Assigned To**: theme-swapper
- **Agent Type**: frontend-architect
- **Parallel**: true (can run alongside restore-custom-vars)
- Run `cd web/frontend && npm install` to install any new dependencies the preset added
- Check if the primitive library changed (e.g., from `@base-ui/react` to `radix-ui` or vice versa)
- If the primitive library changed, note this for the component regeneration step

### 5. Regenerate shadcn UI Components
- **Task ID**: regen-components
- **Depends On**: restore-custom-vars, install-deps
- **Assigned To**: component-updater
- **Agent Type**: frontend-architect
- **Parallel**: false
- Run: `cd web/frontend && pnpm dlx shadcn@latest add button input table badge dialog --overwrite`
- This regenerates all 5 UI components to match the new preset style
- After regeneration, inspect each component for API changes:
  - **button.tsx**: Check if variant names (`default`, `outline`, `secondary`, `ghost`, `destructive`, `link`) and size names (`default`, `xs`, `sm`, `lg`, `icon`, `icon-xs`, `icon-sm`, `icon-lg`) still exist
  - **input.tsx**: Check if the component props interface changed
  - **table.tsx**: Check if exports changed (`Table`, `TableHeader`, `TableBody`, `TableFooter`, `TableHead`, `TableRow`, `TableCell`, `TableCaption`)
  - **badge.tsx**: Check if variant names changed
  - **dialog.tsx**: Check if `DialogContent` still accepts `showCloseButton` prop and same sub-components exist

### 6. Fix Custom Component Breakage
- **Task ID**: fix-custom-components
- **Depends On**: regen-components
- **Assigned To**: component-updater
- **Agent Type**: frontend-architect
- **Parallel**: false
- Audit `search-form.tsx`:
  - Uses `Button` with `variant="ghost"`, `size="icon"` — verify these still exist
  - Uses `Input` — verify props compatibility
- Audit `results-table.tsx`:
  - Uses `Button` with `variant="ghost"`, `size="icon-sm"` — verify this size exists
  - Uses all `Table*` exports — verify they exist
- Audit `detail-modal.tsx`:
  - Uses `Dialog`, `DialogContent` (with `className="sm:max-w-lg"`), `DialogHeader`, `DialogTitle` — verify exports and props
  - Uses `Table*` components — verify compatibility
- Audit `availability-badge.tsx`:
  - Does NOT use shadcn Badge — uses raw Tailwind classes with hardcoded colors
  - No changes expected unless Tailwind utility classes changed
- Audit `page.tsx` (home) and `search/page.tsx`:
  - Check that semantic color classes (`text-foreground`, `bg-card`, `border-border`, `text-muted-foreground`, `bg-muted/30`) still resolve correctly with the new theme
- Fix any issues found

### 7. Clean Up Backup Files
- **Task ID**: cleanup-backups
- **Depends On**: fix-custom-components
- **Assigned To**: component-updater
- **Agent Type**: frontend-architect
- **Parallel**: false
- Remove `web/frontend/app/globals.css.bak` and `web/frontend/components.json.bak`

### 8. Validate Build & UI
- **Task ID**: validate-all
- **Depends On**: fix-custom-components, cleanup-backups
- **Assigned To**: validator
- **Agent Type**: validator
- **Parallel**: false
- Run `cd web/frontend && npm run build` — must complete with zero errors
- Run `cd web/frontend && npx tsc --noEmit` — must have zero type errors
- Verify all expected files exist:
  - `components.json` with new preset style
  - `app/globals.css` with new theme + custom vars
  - All 5 `components/ui/*.tsx` files
  - All 4 custom components unchanged in functionality
- Verify no `.bak` files remain

## Acceptance Criteria
- `pnpm dlx shadcn@latest init --preset b3lV0H9rs --template next` has been run successfully
- `components.json` reflects the new preset configuration
- `globals.css` uses the new preset's design tokens
- Custom CSS variables `--available` and `--connection` are preserved in both `:root` and `.dark`
- All 5 shadcn UI components are regenerated to match the new style
- All 4 custom components work without errors (search-form, results-table, detail-modal, availability-badge)
- `npm run build` completes with zero errors
- TypeScript compilation has zero type errors
- Dark mode still works (html has `dark` class)
- No backup files left in the repo

## Validation Commands
Execute these commands to validate the task is complete:

```bash
# Build must pass
cd C:/Users/jiami/local_workspace/seataero/web/frontend && npm run build

# TypeScript must pass
cd C:/Users/jiami/local_workspace/seataero/web/frontend && npx tsc --noEmit

# Verify custom CSS vars preserved
grep -q "available" C:/Users/jiami/local_workspace/seataero/web/frontend/app/globals.css && echo "PASS: --available found" || echo "FAIL: --available missing"
grep -q "connection" C:/Users/jiami/local_workspace/seataero/web/frontend/app/globals.css && echo "PASS: --connection found" || echo "FAIL: --connection missing"

# Verify no backup files
test ! -f C:/Users/jiami/local_workspace/seataero/web/frontend/app/globals.css.bak && echo "PASS: no css backup" || echo "FAIL: backup exists"
test ! -f C:/Users/jiami/local_workspace/seataero/web/frontend/components.json.bak && echo "PASS: no json backup" || echo "FAIL: backup exists"

# Verify components exist
ls C:/Users/jiami/local_workspace/seataero/web/frontend/components/ui/{button,input,table,badge,dialog}.tsx
```

## Notes
- The project currently has `package-lock.json` (npm), but the user's command uses `pnpm dlx`. The `pnpm dlx` command works like `npx` — it runs the shadcn CLI without requiring pnpm as the project's package manager. Continue using npm for dependency installation unless the preset switches the lockfile.
- The `availability-badge.tsx` component uses hardcoded Tailwind color classes (`bg-neutral-700`, `bg-orange-500/90`, `bg-green-600/90`) rather than shadcn semantic tokens. These will survive the theme swap unchanged. Consider migrating them to semantic tokens in a future task.
- The preset may change the component primitive library (e.g., from `@base-ui/react` to `radix-ui`). If this happens, the regenerated UI components will handle it automatically, but verify imports compile.
- The `layout.tsx` applies the `dark` class to `<html>` statically — there's no theme toggle. The new preset may include light mode variables; this is fine as long as dark mode still works.
