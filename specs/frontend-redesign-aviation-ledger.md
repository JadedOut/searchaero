# Plan: Frontend Redesign — Aviation Ledger Reference

## Task Description
Redesign the Seataero frontend to match the aviation-ledger reference UI. The reference is a dark-themed, premium flight search interface built with raw Tailwind in a single-file Vite/React app. We need to replicate its design using shadcn components in our existing Next.js + shadcn setup. Default to **list view** (not calendar). Keep our existing coloring conventions where there's a conflict; adopt the reference's conventions for new features.

## Objective
When complete, the Seataero frontend will have:
1. A redesigned **home page** with hero title, glassmorphic search card, origin/destination inputs with swap, date range, cabin class selector, and stats cards
2. A redesigned **results page** with sticky filter bar, cabin class filters, max miles slider, direct-flights toggle, view switcher (list default), a full 9-column results table, and pagination footer
3. A **navbar** and **footer** matching the reference's layout and style
4. All built with shadcn/ui components (Input, Button, Badge, Table, Dialog, Switch, Slider, Checkbox, etc.)

## Problem Statement
The current frontend is minimal — a centered search form, a basic results table, and a detail modal. The reference code shows a much richer UI with: glassmorphic nav, hero landing, expanded search form (date range + cabin class), filter controls, view toggle, pagination, airline logos, departure/arrival times, and stats cards. We need to bridge this gap while preserving our existing data model (API-backed, not mock data) and orange theme.

## Solution Approach
- **Component-by-component rebuild** using shadcn primitives (not raw Tailwind divs)
- **Dark mode only** — the reference is dark-only; our app already defaults to dark
- **Color mapping**: Our `--primary` (orange oklch) stays. The reference's `--secondary` (#62df7d green) maps to our `--available`. The reference's `--primary-container` (#f97316 vibrant orange) maps to our `--connection`. New surface colors from the reference get added as CSS custom properties.
- **List view default** — the Calendar/List toggle renders but List is active by default
- **Keep existing API layer** — SearchResult, DetailResponse types unchanged; new UI features (filters, pagination) are client-side only for now
- **Install missing shadcn components** as needed: Switch, Slider, Checkbox, Separator, Tooltip

## Relevant Files
Use these files to complete the task:

**Reference (read-only):**
- `referencecode/aviation-ledger/src/App.tsx` — Complete reference UI (Navbar, HomeView, ResultsView, Footer)
- `referencecode/aviation-ledger/src/index.css` — Reference color tokens and glass effects

**Modify:**
- `web/frontend/app/globals.css` — Add surface colors, glass utilities, aviation gradient, scrollbar styles
- `web/frontend/app/layout.tsx` — Add Navbar and Footer to the layout, update font to Inter
- `web/frontend/app/page.tsx` — Redesign home page with hero + search card + stats
- `web/frontend/app/search/page.tsx` — Redesign results page with filter bar + rich table
- `web/frontend/components/search-form.tsx` — Expand: origin/dest labels, full airport names, date range, cabin class
- `web/frontend/components/results-table.tsx` — Complete rewrite: 9-column table, badges, pagination, alternating rows
- `web/frontend/components/availability-badge.tsx` — Update styling to match reference badges (green/orange with glow)
- `web/frontend/components/detail-modal.tsx` — Minor styling updates to match new theme
- `web/frontend/lib/utils.ts` — May need new formatters (e.g., relative time already exists)

### New Files
- `web/frontend/components/navbar.tsx` — Fixed glassmorphic navigation bar
- `web/frontend/components/footer.tsx` — Footer with branding and links
- `web/frontend/components/filter-bar.tsx` — Sticky filter bar with cabin checkboxes, miles slider, direct toggle, view switcher
- `web/frontend/components/stats-cards.tsx` — Home page metrics cards (network latency, cached routes, data integrity)

### New shadcn components to install
- `switch` — For "Direct flights only" toggle
- `slider` — For "Max Miles" range filter
- `checkbox` — For cabin class filter checkboxes
- `separator` — For visual dividers
- `tooltip` — For icon button tooltips

## Implementation Phases

### Phase 1: Foundation — Theme + Layout Shell
- Add reference surface colors to globals.css as new custom properties
- Add glass-nav, glass-card, aviation-gradient utility classes
- Update font from Source Sans 3 to Inter (matching reference)
- Create Navbar component (shadcn Button for nav items, Bell/User icons)
- Create Footer component
- Wire Navbar + Footer into layout.tsx
- Install missing shadcn components (switch, slider, checkbox, separator, tooltip)

### Phase 2: Core Implementation — Home + Results Redesign
- Redesign home page: hero title "SEATAERO", subtitle, glassmorphic search card
- Expand search form: large airport code inputs with labels, swap button, date range picker placeholder, cabin class selector, gradient search button
- Create stats cards component
- Create filter bar component with cabin checkboxes, max miles slider, direct-flights switch, calendar/list view toggle
- Rewrite results table: 9 columns (date, last seen, flight, departs, arrives, economy, premium, business, first)
- Add pagination footer to results table
- Update availability badges to match reference styling (green glow for saver, orange for premium)

### Phase 3: Integration & Polish
- Ensure search form on results page is embedded in the sticky filter bar (compact mode)
- Wire filter state (client-side only — no API changes needed)
- Add smooth transitions/animations where feasible (can use framer-motion or CSS transitions)
- Test responsive layout at mobile/tablet/desktop breakpoints
- Verify dark mode appearance end-to-end
- Verify detail modal still works with new theme

## Team Orchestration

- You operate as the team lead and orchestrate the team to execute the plan.
- You're responsible for deploying the right team members with the right context to execute the plan.
- IMPORTANT: You NEVER operate directly on the codebase. You use `Task` and `Task*` tools to deploy team members to do the building, validating, testing, deploying, and other tasks.

### Team Members

- Builder
  - Name: theme-builder
  - Role: Set up the design foundation — CSS tokens, utility classes, font, install shadcn components
  - Agent Type: frontend-architect
  - Resume: true

- Builder
  - Name: layout-builder
  - Role: Create Navbar, Footer, and update layout.tsx to include them
  - Agent Type: frontend-architect
  - Resume: true

- Builder
  - Name: home-builder
  - Role: Redesign the home page — hero, search card, stats cards
  - Agent Type: frontend-architect
  - Resume: true

- Builder
  - Name: results-builder
  - Role: Redesign the results page — filter bar, results table, pagination, badges
  - Agent Type: frontend-architect
  - Resume: true

- Builder
  - Name: polish-builder
  - Role: Integration pass — responsive fixes, animation, detail modal alignment, final cleanup
  - Agent Type: frontend-architect
  - Resume: true

- Builder
  - Name: validator
  - Role: Verify all pages render, components work, no build errors, visual check
  - Agent Type: validator
  - Resume: false

## Step by Step Tasks

### 1. Install Missing shadcn Components
- **Task ID**: install-shadcn
- **Depends On**: none
- **Assigned To**: theme-builder
- **Agent Type**: frontend-architect
- **Parallel**: true
- Run `npx shadcn@latest add switch slider checkbox separator tooltip` inside `web/frontend/`
- Verify components appear in `components/ui/`

### 2. Update Theme Foundation
- **Task ID**: update-theme
- **Depends On**: install-shadcn
- **Assigned To**: theme-builder
- **Agent Type**: frontend-architect
- **Parallel**: false
- In `globals.css`, add these reference surface colors as CSS custom properties in both `:root` and `.dark`:
  ```
  --surface: #131313
  --surface-container-lowest: #0e0e0e
  --surface-container-low: #1c1b1b
  --surface-container: #201f1f
  --surface-container-high: #2a2a2a
  --surface-container-highest: #353534
  --on-surface: #e5e2e1
  --on-surface-variant: #e0c0b1
  --outline-dim: #a78b7d
  --outline-variant-dim: #584237
  ```
- Map to the `@theme inline` block so Tailwind classes like `bg-surface-container-low` work
- Add utility classes at the bottom: `.glass-nav`, `.glass-card`, `.aviation-gradient`
- Add custom scrollbar styles
- **Keep existing `--primary`, `--available`, `--connection` values** — these are our colors
- Update font import: switch from Source Sans 3 to Inter in layout.tsx

### 3. Create Navbar Component
- **Task ID**: create-navbar
- **Depends On**: update-theme
- **Assigned To**: layout-builder
- **Agent Type**: frontend-architect
- **Parallel**: true (can run in parallel with create-footer)
- Create `web/frontend/components/navbar.tsx`
- Fixed top, `glass-nav` background, z-50
- Left: Logo "Seataero" (uppercase, orange, tracking-tighter, font-black), nav links (Search, My Bookings, Fleet, Insights) — use shadcn Button variant="ghost"
- Right: Bell icon, User icon — shadcn Button variant="ghost" size="icon"
- Active link gets orange text + bottom border
- Currently only "Search" link is functional (navigates to `/`)
- Responsive: hide nav links on mobile (`hidden md:flex`)

### 4. Create Footer Component
- **Task ID**: create-footer
- **Depends On**: update-theme
- **Assigned To**: layout-builder
- **Agent Type**: frontend-architect
- **Parallel**: true (can run in parallel with create-navbar)
- Create `web/frontend/components/footer.tsx`
- Dark background (`bg-surface-container-lowest` or zinc-950), border-t
- Left: "Seataero" branding, copyright text
- Right: Links (Privacy Policy, Terms of Service, API Access, Support) — placeholder hrefs
- Responsive: stack on mobile, side-by-side on desktop

### 5. Update Layout with Navbar + Footer
- **Task ID**: update-layout
- **Depends On**: create-navbar, create-footer
- **Assigned To**: layout-builder
- **Agent Type**: frontend-architect
- **Parallel**: false
- Wrap page content in `min-h-screen flex flex-col` container
- Add Navbar at top (fixed, so add `pt-16` to main content)
- Add Footer at bottom with `mt-auto`
- Update font from Source Sans 3 to Inter

### 6. Redesign Home Page
- **Task ID**: redesign-home
- **Depends On**: update-layout
- **Assigned To**: home-builder
- **Agent Type**: frontend-architect
- **Parallel**: true (can run in parallel with redesign-results)
- Rewrite `app/page.tsx`:
  - Hero section: `text-6xl md:text-8xl font-black tracking-tighter uppercase italic` title "SEATAERO"
  - Subtitle: "Search United award flight availability with precision ledger data."
  - Search card: `glass-card` wrapper with `bg-surface-container-low` inner
  - Origin/Destination: Large inputs (`text-4xl font-black`) with labels ("ORIGIN", "DESTINATION"), use shadcn Input but styled to be borderless/transparent on the inner field
  - Circular swap button between inputs (shadcn Button size="icon" rounded-full)
  - Date Range box: placeholder showing "Select dates" with Calendar icon
  - Cabin Class box: placeholder showing "All cabins" with LayoutList icon
  - Search button: `aviation-gradient` class, uppercase tracking-widest, with ArrowRight icon
- Create `web/frontend/components/stats-cards.tsx`:
  - 3-column grid of metric cards
  - Network Latency (42 MS), Cached Routes (placeholder), Data Integrity (99.9%)
  - Each: `bg-surface-container-low` card with icon, label, value
  - Use Lucide icons: Activity, Database, ShieldCheck

### 7. Redesign Results Page
- **Task ID**: redesign-results
- **Depends On**: update-layout
- **Assigned To**: results-builder
- **Agent Type**: frontend-architect
- **Parallel**: true (can run in parallel with redesign-home)
- Create `web/frontend/components/filter-bar.tsx`:
  - Sticky bar below navbar (`sticky top-16 z-40`)
  - Left section: Route display (origin → destination), date range, passenger count — all in `bg-surface-container-lowest` pills
  - Right section: Calendar/List view toggle — List active by default, use shadcn Button inside a pill container
  - Below: Filter row with:
    - Cabin class checkboxes (shadcn Checkbox) — Economy, Business, First
    - Max Miles slider (shadcn Slider) — 0 to 120k
    - Direct flights toggle (shadcn Switch)
    - Reset filters button
- Rewrite `web/frontend/components/results-table.tsx`:
  - Use shadcn Table components (Table, TableHeader, TableBody, TableRow, TableHead, TableCell)
  - 9 columns: Date, Last Seen, Flight, Departs, Arrives, Economy, Premium, Business, First
  - Date cell: date text + route below in muted
  - Last Seen: small muted timestamp
  - Flight: flight number (we can skip airline logo for now since our API doesn't return it)
  - Departs/Arrives: time in bold + airport code below
  - Cabin columns: Use updated AvailabilityBadge — green glow for saver/available, orange for premium tiers
  - Alternating row backgrounds
  - Hover effect on rows
  - Pagination footer: "Showing X Results" + "Y Saver Awards Found" (green) + page navigation
- Update `web/frontend/components/availability-badge.tsx`:
  - Green badge: `bg-available/10 text-available border border-available/20` with subtle shadow glow
  - Orange badge: `bg-connection/10 text-connection border border-connection/20`
  - Unavailable: em dash in muted color
- Wire filter-bar + results-table into `app/search/page.tsx`
- Note: Filters are **client-side only** — filter the results array in state. No API changes.
- Note: Our API returns `CabinAvailability { miles, taxes_cents }` not just string miles. The table should use `formatMiles()` to display.

### 8. Polish and Integration
- **Task ID**: polish
- **Depends On**: redesign-home, redesign-results
- **Assigned To**: polish-builder
- **Agent Type**: frontend-architect
- **Parallel**: false
- Add CSS transitions for smooth interactions (hover states, focus states)
- Verify detail modal still works and matches the dark theme
- Test responsive layout at mobile (< 768px), tablet (768-1024px), desktop (> 1024px)
- Fix any spacing, overflow, or alignment issues
- Ensure the search form on the results page is compact (not the full hero version)
- Ensure proper `max-w-[1600px]` container on results page for wide screens

### 9. Validate Everything
- **Task ID**: validate-all
- **Depends On**: polish
- **Assigned To**: validator
- **Agent Type**: validator
- **Parallel**: false
- Run `npm run build` in `web/frontend/` — no TypeScript or build errors
- Visually verify home page has: hero title, search card, stats cards, navbar, footer
- Visually verify results page has: filter bar, 9-column table, pagination, badges
- Verify detail modal opens and displays offerings
- Verify no console errors in browser
- Check that our orange theme (`--primary`) is used, not overwritten

## Acceptance Criteria
- [ ] Home page shows hero title "SEATAERO" with glassmorphic search card
- [ ] Search card has origin/dest with large font, swap button, date range, cabin class, gradient search button
- [ ] Stats cards display below search form on home page
- [ ] Navbar is fixed at top with logo, nav links, bell/user icons
- [ ] Footer displays at bottom with branding and links
- [ ] Results page has sticky filter bar with route display and Calendar/List toggle (List default)
- [ ] Filter controls: cabin class checkboxes, max miles slider, direct flights switch, reset button
- [ ] Results table has 9 columns: Date, Last Seen, Flight, Departs, Arrives, Economy, Premium, Business, First
- [ ] Availability badges: green glow for available, orange for premium/connecting, dash for unavailable
- [ ] Pagination footer shows result count + page navigation
- [ ] Dark mode throughout, using our existing orange primary color
- [ ] `npm run build` passes with no errors
- [ ] Detail modal still works when clicking a result row
- [ ] Responsive layout works at mobile/tablet/desktop

## Validation Commands
Execute these commands to validate the task is complete:

```bash
# Build check
cd C:/Users/jiami/local_workspace/seataero/web/frontend && npm run build

# Verify new components exist
ls web/frontend/components/navbar.tsx
ls web/frontend/components/footer.tsx
ls web/frontend/components/filter-bar.tsx
ls web/frontend/components/stats-cards.tsx

# Verify shadcn components installed
ls web/frontend/components/ui/switch.tsx
ls web/frontend/components/ui/slider.tsx
ls web/frontend/components/ui/checkbox.tsx

# Type check
cd C:/Users/jiami/local_workspace/seataero/web/frontend && npx tsc --noEmit
```

## Notes
- The reference uses Motion (framer-motion) for page transitions. We can add this later or use CSS transitions for now to keep scope manageable.
- The reference has `@google/genai` and `express` as dependencies — we do NOT need these. Our backend is FastAPI.
- Filters (cabin class, max miles, direct flights) are client-side only in this plan. Server-side filtering can be added in a future iteration.
- The reference shows flight numbers and departure/arrival times, but our current API (`SearchResult`) returns `date, last_seen, program, origin, destination, economy, premium, business, first`. We should display what we have and leave flight-level detail (flight number, times) for when the API supports it. Use the date + origin/dest as the primary row identifier.
- The date range and cabin class selectors on the home page are **visual placeholders** for now — functional date/cabin filtering can be wired up in a future iteration.
