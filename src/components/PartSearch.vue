<script setup lang="ts">
import { ref, computed } from 'vue';
import '../types/pywebview';
import type {
  PartSearchResult,
  PartSearchCandidate,
  PartFindExistingResult,
  ExistingSchematicMatch,
  ExistingLibraryMatch,
} from '../types/pywebview';
import type { ImportedComponent } from '../types/importer';

/**
 * PartSearch — structured parametric part-search workflow panel.
 *
 * Drives the four-phase loop documented in
 * `public/agents/skills/part-search.md`:
 *   1. Reuse-first: call `part_find_existing` against the open project so
 *      the user sees parts they already have before downloading anything.
 *   2. Discover: call `part_search` with just the specs to fetch raw web
 *      hits + fuzzy MPN hints.
 *   3. Enrich: re-call `part_search` with the chosen MPNs and the same
 *      `search_id` to get verified Octopart cards.
 *   4. Select → preview & commit: call `importer_import_by_part` for the
 *      chosen MPN, then emit `component-imported` so the existing
 *      ImporterDetails panel handles the preview-then-commit modal.
 *
 * All candidate URLs are run through `safeUrl` so only http(s) links
 * reach `<a href>` (matches the sanitiser used in ComponentSearch.vue).
 */

const emit = defineEmits<{
  (e: 'component-imported', component: ImportedComponent, warnings: string[]): void;
  (e: 'reuse-existing', match: ExistingLibraryMatch | ExistingSchematicMatch): void;
}>();

// ---- form state -----------------------------------------------------------
const specs = ref('');
const refineText = ref('');
const projectPath = ref('');
const isLoading = ref(false);
const isSelecting = ref(false);
const errorMessage = ref('');
const statusMessage = ref('');

// ---- result state ---------------------------------------------------------
const searchId = ref<string | null>(null);
const turn = ref(0);
const candidates = ref<PartSearchCandidate[]>([]);
const verifiedAll = ref(true);
const needsMpnExtraction = ref(false);
const mpnHints = ref<string[]>([]);
const lastSpecs = ref('');

const schematicMatches = ref<ExistingSchematicMatch[]>([]);
const libraryMatches = ref<ExistingLibraryMatch[]>([]);

// "Compare" multi-select state — set of selected MPNs.
const compareSelection = ref<Set<string>>(new Set());

// Per-card refine input ("similar to option 2 but…")
const cardRefineMpn = ref<string | null>(null);
const cardRefineText = ref('');

const hasAnyResults = computed(
  () =>
    candidates.value.length > 0 ||
    schematicMatches.value.length > 0 ||
    libraryMatches.value.length > 0,
);

const compareList = computed(() =>
  candidates.value.filter(c => compareSelection.value.has(c.mpn)),
);

// ---- helpers --------------------------------------------------------------

/** Restrict outbound URLs to http(s) — same policy as ComponentSearch.vue. */
function safeUrl(url: string | undefined | null): string {
  if (!url) return '';
  try {
    const parsed = new URL(url);
    if (parsed.protocol === 'http:' || parsed.protocol === 'https:') {
      return url;
    }
  } catch {
    /* malformed */
  }
  return '';
}

function formatPrice(c: PartSearchCandidate): string {
  if (!c.price) return '';
  const p = c.price;
  const cur = p.currency || 'USD';
  if (typeof p.unit_price !== 'number') return '';
  return `${cur} ${p.unit_price.toFixed(4)} @ ${p.qty ?? 1}`;
}

function resetAll() {
  searchId.value = null;
  turn.value = 0;
  candidates.value = [];
  schematicMatches.value = [];
  libraryMatches.value = [];
  mpnHints.value = [];
  verifiedAll.value = true;
  needsMpnExtraction.value = false;
  compareSelection.value = new Set();
  cardRefineMpn.value = null;
  cardRefineText.value = '';
  errorMessage.value = '';
  statusMessage.value = '';
  refineText.value = '';
}

async function getProjectPath(): Promise<string> {
  if (projectPath.value) return projectPath.value;
  const api = window.pywebview?.api;
  if (!api) return '';
  try {
    const list = await api.get_open_project_paths();
    if (Array.isArray(list) && list.length) {
      projectPath.value = list[0];
    }
  } catch {
    /* no project open */
  }
  return projectPath.value;
}

// ---- main flow ------------------------------------------------------------

/**
 * Phase 1 + 2: reuse-first scan, then discover candidate MPNs from web,
 * then auto-enrich them with Octopart in a follow-up call.
 */
async function runSearch() {
  const q = specs.value.trim();
  if (!q) return;

  const api = window.pywebview?.api;
  if (!api) {
    errorMessage.value = 'Backend API not available.';
    return;
  }

  isLoading.value = true;
  errorMessage.value = '';
  statusMessage.value = 'Checking what you already have…';
  candidates.value = [];
  schematicMatches.value = [];
  libraryMatches.value = [];
  mpnHints.value = [];
  verifiedAll.value = true;
  needsMpnExtraction.value = false;
  compareSelection.value = new Set();
  lastSpecs.value = q;

  try {
    // Phase 2: reuse-first
    const proj = await getProjectPath();
    if (proj) {
      try {
        const existing: PartFindExistingResult = await api.part_find_existing(proj, q, 5);
        if (existing.success) {
          schematicMatches.value = existing.schematic_matches ?? [];
          libraryMatches.value = existing.library_matches ?? [];
        }
      } catch {
        /* non-fatal */
      }
    }

    // Phase 1a: discover
    statusMessage.value = 'Searching the web for candidate parts…';
    const discover: PartSearchResult = await api.part_search(q, null, '', null, 5);
    if (!discover.success) {
      errorMessage.value = discover.error ?? 'Search failed.';
      return;
    }
    searchId.value = discover.search_id ?? null;
    turn.value = discover.turn ?? 1;
    mpnHints.value = discover.mpn_hints ?? [];
    needsMpnExtraction.value = !!discover.needs_mpn_extraction;

    // Phase 1b: enrich any discovered MPN hints (best-effort).
    const hints = (discover.mpn_hints ?? []).slice(0, 5);
    if (hints.length) {
      statusMessage.value = `Looking up ${hints.length} candidate parts on Octopart…`;
      const enriched: PartSearchResult = await api.part_search(
        q,
        hints,
        '',
        searchId.value,
        5,
      );
      if (enriched.success) {
        candidates.value = enriched.candidates ?? [];
        verifiedAll.value = enriched.verified_all ?? false;
        turn.value = enriched.turn ?? turn.value;
      }
    }
  } catch (err) {
    errorMessage.value = `Error: ${err instanceof Error ? err.message : String(err)}`;
  } finally {
    isLoading.value = false;
    statusMessage.value = '';
  }
}

/** Re-run with extra refinement text appended, reusing the same session. */
async function refineSearch() {
  const extra = refineText.value.trim();
  if (!extra) return;
  specs.value = `${specs.value.trim()} (${extra})`;
  refineText.value = '';
  await runSearch();
}

/** Re-run a per-card "similar to option N but…" refinement. */
async function refineCard() {
  const extra = cardRefineText.value.trim();
  const mpn = cardRefineMpn.value;
  if (!extra || !mpn) {
    cardRefineMpn.value = null;
    cardRefineText.value = '';
    return;
  }
  specs.value = `${specs.value.trim()} (similar to ${mpn} but ${extra})`;
  cardRefineMpn.value = null;
  cardRefineText.value = '';
  await runSearch();
}

/** Phase 3: ask the backend for additional candidates beyond the top 5. */
async function moreCandidates() {
  const api = window.pywebview?.api;
  if (!api || !searchId.value) return;
  isLoading.value = true;
  statusMessage.value = 'Fetching more candidates…';
  try {
    const r: PartSearchResult = await api.part_search(
      lastSpecs.value, null, refineText.value || '', searchId.value, 10,
    );
    if (r.success) {
      const fresh = (r.mpn_hints ?? []).filter(
        h => !candidates.value.some(c => c.mpn.toUpperCase() === h.toUpperCase()),
      ).slice(0, 5);
      if (fresh.length) {
        const enriched: PartSearchResult = await api.part_search(
          lastSpecs.value, fresh, '', searchId.value, 10,
        );
        if (enriched.success) {
          // Merge into existing list, deduping by MPN.
          const seen = new Set(candidates.value.map(c => c.mpn.toUpperCase()));
          for (const c of enriched.candidates ?? []) {
            if (!seen.has(c.mpn.toUpperCase())) {
              candidates.value.push(c);
              seen.add(c.mpn.toUpperCase());
            }
          }
          verifiedAll.value = candidates.value.every(c => c.verified);
        }
      }
    }
  } catch (err) {
    errorMessage.value = `Error: ${err instanceof Error ? err.message : String(err)}`;
  } finally {
    isLoading.value = false;
    statusMessage.value = '';
  }
}

function toggleCompare(mpn: string) {
  // Set is reactive when reassigned.
  const next = new Set(compareSelection.value);
  if (next.has(mpn)) next.delete(mpn); else next.add(mpn);
  compareSelection.value = next;
}

/**
 * Phase 4: select a candidate → fetch CAD preview via the existing
 * importer pipeline → emit `component-imported` so the ImporterDetails
 * panel handles the preview / commit-to-library modal.
 */
async function selectCandidate(c: PartSearchCandidate) {
  const api = window.pywebview?.api;
  if (!api) return;
  isSelecting.value = true;
  errorMessage.value = '';
  statusMessage.value = `Fetching CAD data for ${c.mpn}…`;
  try {
    const r = await api.importer_import_by_part(c.mpn, '', c.lcsc_pn || '');
    if (r?.success && r?.component) {
      emit('component-imported', r.component as ImportedComponent, r.warnings ?? []);
    } else {
      errorMessage.value = r?.error || `Could not fetch CAD data for ${c.mpn}.`;
    }
  } catch (err) {
    errorMessage.value = `Error: ${err instanceof Error ? err.message : String(err)}`;
  } finally {
    isSelecting.value = false;
    statusMessage.value = '';
  }
}

function selectExistingLibrary(m: ExistingLibraryMatch) {
  emit('reuse-existing', m);
}

function selectExistingSchematic(m: ExistingSchematicMatch) {
  emit('reuse-existing', m);
}

function handleKeydown(event: KeyboardEvent) {
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault();
    runSearch();
  }
}
</script>

<template>
  <div class="part-search">
    <div class="ps-header">
      <span class="material-icons header-icon">manage_search</span>
      <span class="header-title">Part Search</span>
    </div>

    <!-- Spec input -->
    <div class="ps-input-row">
      <input
        v-model="specs"
        class="ps-input"
        type="text"
        placeholder="e.g. 24-bit ADC, SPI, ≤ $10, single-supply 3.3 V"
        :disabled="isLoading || isSelecting"
        @keydown="handleKeydown"
      />
      <button
        class="ps-btn primary"
        :disabled="isLoading || isSelecting || !specs.trim()"
        @click="runSearch"
        title="Search for candidate parts"
      >
        <span v-if="isLoading" class="material-icons spin">sync</span>
        <span v-else class="material-icons">search</span>
      </button>
      <button
        v-if="hasAnyResults"
        class="ps-btn ghost"
        :disabled="isLoading || isSelecting"
        @click="resetAll"
        title="Clear results"
      >
        <span class="material-icons">restart_alt</span>
      </button>
    </div>

    <!-- Status / error -->
    <div v-if="statusMessage" class="ps-status">
      <span class="material-icons spin">sync</span>
      <span>{{ statusMessage }}</span>
    </div>
    <div v-if="errorMessage" class="ps-error">
      <span class="material-icons">error_outline</span>
      <span>{{ errorMessage }}</span>
    </div>

    <!-- Results scroll area -->
    <div v-if="hasAnyResults" class="ps-results">
      <!-- Reuse-first section -->
      <div
        v-if="libraryMatches.length || schematicMatches.length"
        class="ps-section reuse-section"
      >
        <div class="section-title">
          <span class="material-icons">recycling</span>
          Already in your project
        </div>

        <div
          v-for="m in libraryMatches"
          :key="`lib-${m.lib_id}`"
          class="reuse-card"
        >
          <div class="reuse-card-main">
            <div class="reuse-lib-id">{{ m.lib_id }}</div>
            <div v-if="m.description" class="reuse-desc">{{ m.description }}</div>
            <div v-if="m.mpn || m.manufacturer" class="reuse-meta">
              <span v-if="m.manufacturer">{{ m.manufacturer }}</span>
              <span v-if="m.mpn"> · {{ m.mpn }}</span>
            </div>
          </div>
          <button
            class="ps-btn small primary"
            title="Use this lib_id"
            @click="selectExistingLibrary(m)"
          >
            Use
          </button>
        </div>

        <div
          v-for="m in schematicMatches"
          :key="`sch-${m.reference}-${m.schematic}`"
          class="reuse-card"
        >
          <div class="reuse-card-main">
            <div class="reuse-lib-id">{{ m.reference }} — {{ m.value }}</div>
            <div v-if="m.footprint" class="reuse-desc">{{ m.footprint }}</div>
            <div class="reuse-meta">on {{ m.schematic }}</div>
          </div>
          <button
            class="ps-btn small ghost"
            title="Show this schematic symbol"
            @click="selectExistingSchematic(m)"
          >
            Show
          </button>
        </div>
      </div>

      <!-- Web candidates section -->
      <div v-if="candidates.length" class="ps-section">
        <div class="section-title">
          <span class="material-icons">storefront</span>
          Top {{ candidates.length }} candidate{{ candidates.length === 1 ? '' : 's' }}
          <span
            v-if="!verifiedAll"
            class="warn-badge"
            title="Some candidates could not be verified on Octopart"
          >
            unverified
          </span>
        </div>

        <article
          v-for="(c, idx) in candidates"
          :key="`cand-${c.mpn}`"
          class="cand-card"
          :class="{ unverified: !c.verified, selected: compareSelection.has(c.mpn) }"
        >
          <header class="cand-header">
            <div class="cand-index">{{ idx + 1 }}</div>
            <div class="cand-title">
              <div class="cand-mpn">{{ c.mpn }}</div>
              <div v-if="c.manufacturer" class="cand-mfr">{{ c.manufacturer }}</div>
            </div>
            <span v-if="c.verified" class="verify-chip ok" title="Verified on Octopart">
              <span class="material-icons">verified</span>
            </span>
            <span v-else class="verify-chip warn" title="Octopart could not verify this MPN">
              <span class="material-icons">help_outline</span>
            </span>
          </header>

          <p v-if="c.description" class="cand-desc">{{ c.description }}</p>

          <ul
            v-if="c.key_specs && Object.keys(c.key_specs).length"
            class="cand-specs"
          >
            <li v-for="(v, k) in c.key_specs" :key="k">
              <span class="spec-key">{{ k }}</span>
              <span class="spec-val">{{ v }}</span>
            </li>
          </ul>

          <div v-if="formatPrice(c)" class="price-chip">
            {{ formatPrice(c) }}
          </div>

          <ul
            v-if="c.warnings && c.warnings.length"
            class="cand-warnings"
          >
            <li v-for="(w, i) in c.warnings" :key="i">{{ w }}</li>
          </ul>

          <div class="cand-links">
            <a
              v-if="safeUrl(c.datasheet_url)"
              :href="safeUrl(c.datasheet_url)"
              target="_blank"
              rel="noopener noreferrer"
              class="link-btn"
            >
              <span class="material-icons">picture_as_pdf</span>
              Datasheet
            </a>
            <a
              v-if="safeUrl(c.product_url)"
              :href="safeUrl(c.product_url)"
              target="_blank"
              rel="noopener noreferrer"
              class="link-btn"
            >
              <span class="material-icons">open_in_new</span>
              Product page
            </a>
          </div>

          <footer class="cand-footer">
            <button
              class="ps-btn small primary"
              :disabled="isSelecting || !c.verified"
              :title="c.verified ? 'Preview & import this part' : 'Cannot import an unverified MPN'"
              @click="selectCandidate(c)"
            >
              <span class="material-icons">download</span>
              Select
            </button>
            <button
              class="ps-btn small ghost"
              :disabled="isLoading || isSelecting"
              title="Show similar parts but with a different constraint"
              @click="cardRefineMpn = c.mpn"
            >
              Similar but…
            </button>
            <button
              class="ps-btn small"
              :class="compareSelection.has(c.mpn) ? 'primary' : 'ghost'"
              title="Add to compare"
              @click="toggleCompare(c.mpn)"
            >
              <span class="material-icons">{{ compareSelection.has(c.mpn) ? 'check_box' : 'check_box_outline_blank' }}</span>
              Compare
            </button>
          </footer>

          <!-- Inline per-card refinement input -->
          <div v-if="cardRefineMpn === c.mpn" class="card-refine">
            <input
              v-model="cardRefineText"
              type="text"
              class="ps-input small"
              :placeholder="`Similar to ${c.mpn} but…`"
              @keydown.enter.prevent="refineCard()"
              @keydown.esc="cardRefineMpn = null"
            />
            <button class="ps-btn small primary" @click="refineCard">Refine</button>
            <button class="ps-btn small ghost" @click="cardRefineMpn = null">Cancel</button>
          </div>
        </article>

        <!-- Toolbar actions -->
        <div class="ps-toolbar">
          <input
            v-model="refineText"
            class="ps-input small"
            type="text"
            placeholder="Narrow further (e.g. cheaper, lower power, smaller package)"
            :disabled="isLoading || isSelecting"
            @keydown.enter.prevent="refineSearch"
          />
          <button
            class="ps-btn small primary"
            :disabled="isLoading || isSelecting || !refineText.trim()"
            @click="refineSearch"
          >
            Narrow
          </button>
          <button
            class="ps-btn small ghost"
            :disabled="isLoading || isSelecting || !searchId"
            title="Fetch more candidates"
            @click="moreCandidates"
          >
            <span class="material-icons">add</span>
            More
          </button>
        </div>

        <!-- Compare summary -->
        <div v-if="compareList.length >= 2" class="ps-compare">
          <div class="section-title">
            <span class="material-icons">compare_arrows</span>
            Comparing {{ compareList.length }}
          </div>
          <table class="compare-table">
            <thead>
              <tr>
                <th>Field</th>
                <th v-for="c in compareList" :key="c.mpn">{{ c.mpn }}</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td>Manufacturer</td>
                <td v-for="c in compareList" :key="c.mpn">{{ c.manufacturer || '—' }}</td>
              </tr>
              <tr>
                <td>Description</td>
                <td v-for="c in compareList" :key="c.mpn">{{ c.description || '—' }}</td>
              </tr>
              <tr>
                <td>DigiKey</td>
                <td v-for="c in compareList" :key="c.mpn">{{ c.digikey_pn || '—' }}</td>
              </tr>
              <tr>
                <td>LCSC</td>
                <td v-for="c in compareList" :key="c.mpn">{{ c.lcsc_pn || '—' }}</td>
              </tr>
              <tr>
                <td>Mouser</td>
                <td v-for="c in compareList" :key="c.mpn">{{ c.mouser_pn || '—' }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <!-- No candidates but had hints — backend couldn't verify. -->
      <div
        v-else-if="needsMpnExtraction && mpnHints.length"
        class="ps-section ps-info"
      >
        <span class="material-icons">info</span>
        <div>
          Found {{ mpnHints.length }} possible MPN{{ mpnHints.length === 1 ? '' : 's' }}
          but Octopart couldn't verify any of them. Try refining your specs
          or open the chat agent to dig further.
        </div>
      </div>
      <div v-else-if="needsMpnExtraction" class="ps-section ps-info">
        <span class="material-icons">info</span>
        <div>
          Web search returned no extractable manufacturer part numbers.
          Try a more specific spec query.
        </div>
      </div>
    </div>

    <!-- Empty state -->
    <div v-else-if="!isLoading && !errorMessage" class="ps-empty">
      <span class="material-icons empty-icon">manage_search</span>
      <p>
        Describe the part you need — the assistant will check what's already
        in your project, then propose verified Octopart candidates.
      </p>
      <p class="ps-example">
        Example: <em>buck converter, 5 V → 3.3 V, ≥ 1 A, SOT-23-6</em>
      </p>
    </div>
  </div>
</template>

<style scoped>
.part-search {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  padding: 0.75rem;
  overflow: hidden;
  min-height: 0;
}

.ps-header {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  font-size: 0.75rem;
  font-weight: 700;
  color: var(--text-secondary);
  text-transform: uppercase;
  letter-spacing: 0.05em;
  padding-bottom: 0.4rem;
  border-bottom: 1px solid var(--border-color);
  flex-shrink: 0;
}

.header-icon { font-size: 1rem; }

.ps-input-row {
  display: flex;
  gap: 0.3rem;
  flex-shrink: 0;
}

.ps-input {
  flex: 1;
  min-width: 0;
  padding: 0.4rem 0.5rem;
  border: 1px solid var(--border-color);
  border-radius: var(--radius-sm);
  background: var(--bg-input);
  color: var(--text-primary);
  font-size: 0.78rem;
  outline: none;
  transition: border-color 0.15s;
}

.ps-input.small { font-size: 0.72rem; padding: 0.3rem 0.45rem; }

.ps-input:focus { border-color: var(--accent-color); }
.ps-input:disabled { opacity: 0.6; }

.ps-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 0.25rem;
  padding: 0.4rem 0.6rem;
  border-radius: var(--radius-sm);
  border: 1px solid var(--border-color);
  background: var(--bg-secondary);
  color: var(--text-primary);
  font-size: 0.75rem;
  font-weight: 600;
  cursor: pointer;
  transition: background 0.15s, color 0.15s, border-color 0.15s;
  white-space: nowrap;
}

.ps-btn.small { padding: 0.25rem 0.5rem; font-size: 0.7rem; }

.ps-btn.primary {
  background: var(--accent-color);
  color: #fff;
  border-color: var(--accent-color);
}

.ps-btn.primary:hover:not(:disabled) { background: var(--accent-hover); }

.ps-btn.ghost {
  background: transparent;
}

.ps-btn:hover:not(:disabled) { border-color: var(--accent-color); }

.ps-btn:disabled { opacity: 0.5; cursor: not-allowed; }

.ps-btn .material-icons { font-size: 0.95rem; }
.ps-btn.small .material-icons { font-size: 0.85rem; }

.spin { animation: spin 1s linear infinite; }
@keyframes spin {
  from { transform: rotate(0deg); }
  to   { transform: rotate(360deg); }
}

.ps-status,
.ps-error {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  padding: 0.4rem 0.5rem;
  border-radius: var(--radius-sm);
  font-size: 0.75rem;
  flex-shrink: 0;
}

.ps-status {
  color: var(--text-secondary);
  background: var(--bg-secondary);
}

.ps-error {
  color: #e05252;
  background: rgba(224, 82, 82, 0.1);
}

.ps-error .material-icons,
.ps-status .material-icons { font-size: 0.95rem; }

.ps-results {
  display: flex;
  flex-direction: column;
  gap: 0.6rem;
  overflow-y: auto;
  flex: 1;
  min-height: 0;
}

.ps-section {
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
}

.section-title {
  display: flex;
  align-items: center;
  gap: 0.3rem;
  font-size: 0.7rem;
  font-weight: 700;
  color: var(--text-secondary);
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

.section-title .material-icons { font-size: 0.95rem; }

.warn-badge {
  margin-left: 0.3rem;
  padding: 0.05rem 0.4rem;
  border-radius: 999px;
  font-size: 0.6rem;
  font-weight: 700;
  background: rgba(255, 165, 0, 0.18);
  color: #c98000;
  border: 1px solid rgba(255, 165, 0, 0.35);
  letter-spacing: 0.04em;
}

/* ---- Reuse cards ---- */

.reuse-section { gap: 0.3rem; }

.reuse-card {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.4rem 0.5rem;
  border: 1px solid var(--border-color);
  border-radius: var(--radius-sm);
  background: rgba(80, 200, 120, 0.05);
}

.reuse-card-main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 0.1rem;
}

.reuse-lib-id {
  font-family: monospace;
  font-size: 0.75rem;
  font-weight: 700;
  color: var(--text-primary);
  word-break: break-all;
}

.reuse-desc {
  font-size: 0.7rem;
  color: var(--text-primary);
}

.reuse-meta {
  font-size: 0.65rem;
  color: var(--text-secondary);
}

/* ---- Candidate cards ---- */

.cand-card {
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
  padding: 0.5rem 0.6rem;
  border: 1px solid var(--border-color);
  border-radius: var(--radius-md);
  background: var(--bg-secondary);
  transition: border-color 0.15s, box-shadow 0.15s;
}

.cand-card.selected { border-color: var(--accent-color); }
.cand-card.unverified { background: var(--bg-tertiary); }

.cand-header {
  display: flex;
  align-items: flex-start;
  gap: 0.5rem;
}

.cand-index {
  flex-shrink: 0;
  width: 1.3rem;
  height: 1.3rem;
  border-radius: 50%;
  background: var(--accent-color);
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 0.7rem;
  font-weight: 700;
}

.cand-title { flex: 1; min-width: 0; }

.cand-mpn {
  font-family: monospace;
  font-size: 0.85rem;
  font-weight: 700;
  color: var(--text-primary);
  word-break: break-all;
}

.cand-mfr {
  font-size: 0.7rem;
  color: var(--text-secondary);
}

.verify-chip {
  flex-shrink: 0;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 1.3rem;
  height: 1.3rem;
  border-radius: 50%;
}

.verify-chip.ok { color: #29a36a; background: rgba(41, 163, 106, 0.12); }
.verify-chip.warn { color: #c98000; background: rgba(255, 165, 0, 0.15); }
.verify-chip .material-icons { font-size: 0.95rem; }

.cand-desc {
  font-size: 0.72rem;
  color: var(--text-primary);
  line-height: 1.4;
  margin: 0;
}

.cand-specs {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  grid-template-columns: 1fr;
  gap: 0.15rem;
  font-size: 0.7rem;
}

.cand-specs li {
  display: flex;
  justify-content: space-between;
  gap: 0.5rem;
  border-bottom: 1px dashed var(--border-color);
  padding: 0.1rem 0;
}

.spec-key { color: var(--text-secondary); text-transform: capitalize; }
.spec-val { color: var(--text-primary); font-weight: 600; }

.price-chip {
  display: inline-block;
  align-self: flex-start;
  padding: 0.15rem 0.4rem;
  border-radius: 999px;
  font-size: 0.65rem;
  font-weight: 700;
  background: rgba(88, 101, 242, 0.12);
  color: var(--accent-color);
  border: 1px solid rgba(88, 101, 242, 0.3);
}

.cand-warnings {
  margin: 0;
  padding-left: 1rem;
  font-size: 0.65rem;
  color: #c98000;
}

.cand-links {
  display: flex;
  gap: 0.4rem;
  flex-wrap: wrap;
}

.link-btn {
  display: inline-flex;
  align-items: center;
  gap: 0.2rem;
  padding: 0.2rem 0.45rem;
  border: 1px solid var(--border-color);
  border-radius: var(--radius-sm);
  background: var(--bg-input);
  color: var(--accent-color);
  font-size: 0.7rem;
  font-weight: 600;
  text-decoration: none;
}

.link-btn:hover { border-color: var(--accent-color); }
.link-btn .material-icons { font-size: 0.85rem; }

.cand-footer {
  display: flex;
  gap: 0.3rem;
  flex-wrap: wrap;
  margin-top: 0.2rem;
}

.card-refine {
  display: flex;
  gap: 0.3rem;
  align-items: center;
  margin-top: 0.2rem;
}

.card-refine .ps-input { flex: 1; }

.ps-toolbar {
  display: flex;
  gap: 0.3rem;
  align-items: center;
  margin-top: 0.4rem;
  padding-top: 0.4rem;
  border-top: 1px solid var(--border-color);
}

.ps-toolbar .ps-input { flex: 1; }

.ps-compare {
  margin-top: 0.6rem;
  display: flex;
  flex-direction: column;
  gap: 0.3rem;
}

.compare-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.68rem;
}

.compare-table th,
.compare-table td {
  border: 1px solid var(--border-color);
  padding: 0.25rem 0.4rem;
  text-align: left;
  vertical-align: top;
}

.compare-table th {
  background: var(--bg-tertiary);
  font-weight: 700;
}

.compare-table td:first-child {
  font-weight: 600;
  color: var(--text-secondary);
  background: var(--bg-secondary);
  white-space: nowrap;
}

.ps-info {
  flex-direction: row;
  align-items: flex-start;
  gap: 0.4rem;
  padding: 0.5rem;
  background: var(--bg-secondary);
  border: 1px dashed var(--border-color);
  border-radius: var(--radius-sm);
  font-size: 0.72rem;
  color: var(--text-secondary);
}

.ps-info .material-icons {
  font-size: 1rem;
  color: var(--accent-color);
  flex-shrink: 0;
}

.ps-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
  gap: 0.5rem;
  color: var(--text-secondary);
  padding: 1.5rem 0.5rem;
}

.empty-icon {
  font-size: 2.5rem;
  opacity: 0.4;
}

.ps-empty p {
  font-size: 0.75rem;
  line-height: 1.5;
  max-width: 220px;
}

.ps-example {
  font-size: 0.7rem !important;
  opacity: 0.85;
}
</style>
