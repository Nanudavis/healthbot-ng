import { useEffect, useState } from 'react'
import {
  Bar,
  BarChart,
  CartesianGrid,
  Line,
  LineChart,
  Legend,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

// Triage semantic colours — reserved for triage outcomes only (UX Rule 01)
const TRIAGE = {
  SELF_CARE: { label: 'SELF-CARE', color: '#2E7D32', badge: 'b-self' },
  CLINIC: { label: 'VISIT CLINIC', color: '#B45309', badge: 'b-clinic' },
  EMERGENCY: { label: 'EMERGENCY', color: '#B91C1C', badge: 'b-emerg' },
}
const LANG_COLORS = {
  pidgin: '#12756B',
  english: '#0B3B37',
  hausa: '#008751',
  yoruba: '#7FC8BD',
  igbo: '#5C9E93',
}
const REFRESH_MS = 30_000

// Windows the whole console shares, so one selector governs every page.
const RANGES = [
  { id: 1, label: '24h' },
  { id: 7, label: '7 days' },
  { id: 30, label: '30 days' },
  { id: 0, label: 'All time' },
]

// Distinct enough to follow nine lines at once, and deliberately not
// the triage palette — those three colours mean urgency, nothing else.
const SERIES_COLORS = [
  '#12756B', '#0B3B37', '#008751', '#7FC8BD', '#5C9E93',
  '#3F6F68', '#9DB8B4', '#1F5F57', '#84B5AD',
]

async function getJSON(path) {
  const res = await fetch(path)
  if (!res.ok) throw new Error(`${path}: ${res.status}`)
  return res.json()
}

const PAGES = [
  {
    id: 'overview',
    label: 'Overview',
    title: 'Research overview',
    subtitle: 'Pseudonymised triage records · not population surveillance',
  },
  {
    id: 'symptoms',
    label: 'Symptom trends',
    title: 'Symptom trends',
    subtitle: 'Descriptive research signals · not disease-prevalence estimates',
  },
  {
    id: 'geography',
    label: 'States & LGAs',
    title: 'States & LGAs',
    subtitle: 'Sample-registry routing data · not national coverage',
  },
  {
    id: 'languages',
    label: 'Languages',
    title: 'Language analysis',
    subtitle: 'Descriptive subsets · not comparative language validation',
  },
  {
    id: 'facilities',
    label: 'Facility routing',
    title: 'Facility routing',
    subtitle: 'Twelve-site demonstration registry · verify before external use',
  },
  {
    id: 'validation',
    label: 'Clinician review',
    title: 'Clinician label-review workflow',
    subtitle: 'Research workflow · clinical validation is not yet established',
  },
  {
    id: 'sus',
    label: 'Usability (SUS)',
    title: 'Usability study workflow — SUS',
    subtitle: 'Instrument implemented · participant usability evidence not yet collected',
  },
  {
    id: 'export',
    label: 'Data export',
    title: 'Data export',
    subtitle: 'Pseudonymised triage export · stable hashes remain linkable',
  },
  {
    id: 'knowledge',
    label: 'Knowledge base',
    title: 'Knowledge base — selected protocols',
    subtitle: 'Prototype corpus · strict grounding remains a pre-deployment requirement',
  },
  {
    id: 'settings',
    label: 'Settings',
    title: 'Settings — AI provider',
    subtitle: 'Configuration interface · every provider change requires re-evaluation',
  },
]

export default function App() {
  const [page, setPage] = useState('overview')
  const [range, setRange] = useState(30)
  const [data, setData] = useState({})
  const [error, setError] = useState(null)
  const [security, setSecurity] = useState(null)
  const [auth, setAuth] = useState(null)

  useEffect(() => {
    getJSON('/api/security/status').then(setSecurity).catch(() => {})
  }, [])

  useEffect(() => {
    getJSON('/api/auth/status')
      .then((d) => setAuth(!!d.authenticated))
      .catch(() => setAuth(true)) // unreachable server should not block the UI
  }, [])

  useEffect(() => {
    if (auth === null || auth === false) return
    let alive = true
    const load = () =>
      Promise.all([
        getJSON(`/api/stats/summary?days=${range}`),
        getJSON(`/api/stats/daily?days=${range || 30}`),
        getJSON('/api/stats/recent?limit=8'),
        getJSON(`/api/stats/symptoms?days=${range}`),
        getJSON(`/api/stats/symptom-series?days=${range || 90}`),
        getJSON(`/api/stats/languages?days=${range}`),
        getJSON(`/api/stats/geography?days=${range}`),
        getJSON(`/api/stats/facilities?days=${range}`),
        getJSON(`/api/stats/routing-misses?days=${range}`),
        getJSON(`/api/stats/alerts?days=${range || 14}`),
      ])
        .then(
          ([summary, daily, recent, symptoms, series, languages, geography, facilities, misses, alerts]) => {
          if (!alive) return
          setData({ summary, daily, recent, symptoms, series, languages, geography, facilities, misses, alerts })
          setError(null)
          }
        )
        .catch((e) => alive && setError(String(e)))
    load()
    const timer = setInterval(load, REFRESH_MS)
    return () => {
      alive = false
      clearInterval(timer)
    }
  }, [range, auth])

  const current = PAGES.find((p) => p.id === page) ?? PAGES[0]

  if (auth === false) {
    return (
      <LoginScreen
        onLogin={() => {
          setAuth(true)
          setData({})
        }}
      />
    )
  }

  return (
    <div className="dash">
      <Sidebar page={page} onNavigate={setPage} />
      <div className="main">
        <div className="topbar">
          <div>
            <h3>{current.title}</h3>
            <div className="sub">{current.subtitle}</div>
          </div>
          <div className="topbar-right">
            <div className="row-actions">
              {data.alerts && (
                <button
                  type="button"
                  className={`chip${data.alerts.alerts.length ? ' chip-on' : ''}`}
                  style={data.alerts.alerts.length ? { background: '#B453091a', color: '#B45309', borderColor: '#B45309' } : undefined}
                  onClick={() => setPage('overview')}
                  title="IDSR-style threshold checks on the trailing window"
                >
                  {data.alerts.alerts.length
                    ? `⚠ ${data.alerts.alerts.length} alert${data.alerts.alerts.length === 1 ? '' : 's'}`
                    : 'No alerts'}
                </button>
              )}
              {RANGES.map((r) => (
                <button
                  key={r.id}
                  type="button"
                  className={`chip${range === r.id ? ' chip-on' : ''}`}
                  onClick={() => setRange(r.id)}
                >
                  {r.label}
                </button>
              ))}
            </div>
            <div className="pill">
              <span className="live" />
              Live · auto-refresh 30s
            </div>
          </div>
        </div>

        {security?.warnings?.length > 0 && (
          <div className="banner-warn" role="status">
            <b>Security notice</b>
            {security.warnings.map((w) => (
              <p key={w}>{w}</p>
            ))}
          </div>
        )}

        {error && <div className="empty">API unreachable: {error}</div>}
        {!error && !data.summary && <div className="empty">Loading…</div>}
        {data.summary && (
          <>
            {page === 'overview' && <Overview data={data} />}
            {page === 'symptoms' && <SymptomTrends symptoms={data.symptoms} series={data.series} />}
            {page === 'geography' && <Geography geography={data.geography} />}
            {page === 'languages' && <Languages languages={data.languages} />}
            {page === 'facilities' && <FacilityRouting facilities={data.facilities} />}
            {page === 'validation' && <ClinicalValidation />}
            {page === 'sus' && <SusStudy />}
            {page === 'export' && <DataExport summary={data.summary} />}
            {page === 'knowledge' && <KnowledgeBase />}
            {page === 'settings' && <Settings />}
          </>
        )}
      </div>
    </div>
  )
}

function LoginScreen({ onLogin }) {
  const [token, setToken] = useState('')
  const [err, setErr] = useState(null)
  const [busy, setBusy] = useState(false)

  const submit = async (e) => {
    e.preventDefault()
    setBusy(true)
    setErr(null)
    try {
      const body = new FormData()
      body.append('token', token)
      const res = await fetch('/api/auth/login', { method: 'POST', body })
      if (!res.ok) {
        const j = await res.json().catch(() => ({}))
        throw new Error(j.detail || res.status)
      }
      onLogin()
    } catch (e2) {
      setErr(String(e2.message ?? e2))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="login-wrap">
      <div className="login-card">
        <div className="login-brand">
          <div className="dot">H</div>
          <div>
            <b>HealthBot NG</b>
            <small>RESEARCH CONSOLE</small>
          </div>
        </div>
        <h3 style={{ fontSize: 19 }}>Sign in to the console</h3>
        <p className="muted" style={{ fontSize: 13, marginBottom: 18 }}>
          Enter the admin token to view prototype research data.
        </p>
        <form onSubmit={submit}>
          <label className="f" style={{ marginBottom: 6 }}>Admin token</label>
          <input
            className="inp"
            type="password"
            placeholder="Enter the admin token"
            value={token}
            onChange={(e) => setToken(e.target.value)}
            autoFocus
          />
          <button type="submit" className="btn" disabled={busy} style={{ width: '100%', marginTop: 14 }}>
            {busy ? 'Signing in…' : 'Sign in'}
          </button>
          {err && <p className="msg-err" style={{ marginTop: 12 }}>{err}</p>}
        </form>
      </div>
    </div>
  )
}

function Sidebar({ page, onNavigate }) {
  return (
    <aside className="side">
      <div className="brand">
        <div className="dot">H</div>
        <div>
          <b>HealthBot NG</b>
          <small>RESEARCH CONSOLE</small>
        </div>
      </div>
      {PAGES.map((p) => (
        <button
          key={p.id}
          type="button"
          className={`nav-item${p.id === page ? ' on' : ''}`}
          aria-current={p.id === page ? 'page' : undefined}
          onClick={() => onNavigate(p.id)}
        >
          {p.label}
        </button>
      ))}
    </aside>
  )
}

function Overview({ data }) {
  return (
    <>
      <AlertsPanel alerts={data.alerts} />
      <Kpis summary={data.summary} />
      <TriageLanes byLevel={data.summary.by_level} />
      <div className="grid2">
        <div className="panel">
          <h4>
            Triage outcomes · last 7 days <span>· all channels</span>
          </h4>
          <DailyChart daily={data.daily} />
        </div>
        <div>
          <div className="panel" style={{ marginBottom: 16 }}>
            <h4>Language mix</h4>
            <LanguageMix byLanguage={data.summary.by_language} />
          </div>
          <div className="panel">
            <h4>
              Recent cases <span>(pseudonymised)</span>
            </h4>
            <RecentCases recent={data.recent} />
          </div>
        </div>
      </div>
    </>
  )
}

function AlertsPanel({ alerts }) {
  if (!alerts) return null
  const list = alerts.alerts ?? []
  if (!list.length) {
    return (
      <div className="panel" style={{ marginBottom: 16 }}>
        <h4>
          Threshold alerts <span>· IDSR-style 2× rule, trailing {alerts.window_days}-day window</span>
        </h4>
        <p className="muted" style={{ fontSize: 13, marginTop: 8 }}>
          No threshold breaches in this window ({alerts.checked} series checked).
        </p>
      </div>
    )
  }
  return (
    <div className="panel" style={{ marginBottom: 16, borderColor: '#B45309' }}>
      <h4>
        Threshold alerts <span>· community signal, requires verification</span>
      </h4>
      {list.map((a, i) => (
        <div className="alert-row" key={i}>
          <span className="badge b-clinic">ALERT</span>
          <span style={{ fontSize: 13 }}>{a.message}</span>
        </div>
      ))}
    </div>
  )
}

function SymptomTrends({ symptoms, series }) {
  if (!symptoms?.length) return <div className="empty">No symptom data yet.</div>
  const chart = symptoms.map((s) => ({
    name: s.symptom,
    SELF_CARE: s.SELF_CARE,
    CLINIC: s.CLINIC,
    EMERGENCY: s.EMERGENCY,
  }))
  return (
    <>
      {series?.series?.length > 0 && (
        <div className="panel" style={{ marginBottom: 16 }}>
          <h4>
            Cases over time{' '}
            <span>· per day, by symptom — a rising line is what to act on</span>
          </h4>
          <ResponsiveContainer width="100%" height={300}>
            <LineChart
              data={series.series.map((d) => ({ ...d, day: d.date.slice(5) }))}
              margin={{ top: 4, right: 12, left: -18 }}
            >
              <CartesianGrid strokeDasharray="3 3" stroke="#DCE6E4" />
              <XAxis dataKey="day" tick={{ fontSize: 11 }} minTickGap={18} />
              <YAxis tick={{ fontSize: 11 }} allowDecimals={false} />
              <Tooltip />
              <Legend wrapperStyle={{ fontSize: 11 }} />
              {series.categories.map((c, i) => (
                <Line
                  key={c}
                  type="monotone"
                  dataKey={c}
                  stroke={SERIES_COLORS[i % SERIES_COLORS.length]}
                  strokeWidth={2}
                  dot={false}
                />
              ))}
            </LineChart>
          </ResponsiveContainer>

          {series.rising?.length > 0 && (
            <div className="rising">
              <b>Rising in this window</b>
              <ul>
                {series.rising.map((r) => (
                  <li key={r.symptom}>
                    {r.symptom}: {r.earlier} → <b>{r.recent}</b>
                    {r.change !== null && ` (+${Math.round(r.change * 100)}%)`}
                  </li>
                ))}
              </ul>
              <p className="muted">
                Comparing the earliest and most recent third of the window.
                A signal to look at, not a confirmed outbreak.
              </p>
            </div>
          )}
        </div>
      )}

      <div className="panel" style={{ marginBottom: 16 }}>
        <h4>
          Reported symptoms <span>· grouped by triage outcome</span>
        </h4>
        <ResponsiveContainer width="100%" height={Math.max(220, chart.length * 46)}>
          <BarChart data={chart} layout="vertical" margin={{ left: 6, right: 16 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#DCE6E4" />
            <XAxis type="number" tick={{ fontSize: 11 }} allowDecimals={false} />
            <YAxis type="category" dataKey="name" width={168} tick={{ fontSize: 11 }} />
            <Tooltip />
            <Legend wrapperStyle={{ fontSize: 12 }} />
            <Bar dataKey="SELF_CARE" name="Self-care" stackId="a" fill={TRIAGE.SELF_CARE.color} />
            <Bar dataKey="CLINIC" name="Clinic" stackId="a" fill={TRIAGE.CLINIC.color} />
            <Bar dataKey="EMERGENCY" name="Emergency" stackId="a" fill={TRIAGE.EMERGENCY.color} />
          </BarChart>
        </ResponsiveContainer>
      </div>
      <div className="panel">
        <h4>Detail</h4>
        <table className="tbl">
          <thead>
            <tr>
              <th>Symptom</th>
              <th className="num">Total</th>
              <th className="num">Emergency</th>
              <th>Languages</th>
            </tr>
          </thead>
          <tbody>
            {symptoms.map((s) => (
              <tr key={s.symptom}>
                <td>{s.symptom}</td>
                <td className="num mono">{s.total}</td>
                <td className="num mono" style={{ color: TRIAGE.EMERGENCY.color }}>
                  {s.EMERGENCY}
                </td>
                <td className="muted">
                  {Object.entries(s.languages)
                    .sort((a, b) => b[1] - a[1])
                    .map(([l, n]) => `${l} ${n}`)
                    .join(' · ')}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  )
}

function Geography({ geography }) {
  if (!geography?.length)
    return (
      <div className="empty">
        No referrals yet. States appear once patients share a location and get
        routed to a facility.
      </div>
    )
  return geography.map((s) => (
    <div className="panel" style={{ marginBottom: 16 }} key={s.state}>
      <h4>
        {s.state} <span>· {s.total} referrals</span>
      </h4>
      {s.EMERGENCY > 0 && (
        <div className="note-emerg">{s.EMERGENCY} emergency referrals</div>
      )}
      <table className="tbl">
        <thead>
          <tr>
            <th>LGA</th>
            <th className="num">Referrals</th>
            <th>Share</th>
          </tr>
        </thead>
        <tbody>
          {s.lgas.map((l) => (
            <tr key={l.lga}>
              <td>{l.lga}</td>
              <td className="num mono">{l.count}</td>
              <td style={{ width: '45%' }}>
                <div className="bar">
                  <i
                    style={{
                      width: `${(l.count / s.total) * 100}%`,
                      background: '#12756B',
                    }}
                  />
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  ))
}

function Languages({ languages }) {
  if (!languages?.length) return <div className="empty">No sessions yet.</div>
  return (
    <>
      <div className="panel" style={{ marginBottom: 16 }}>
        <h4>
          Sessions by language <span>· split by channel</span>
        </h4>
        <ResponsiveContainer width="100%" height={260}>
          <BarChart data={languages} margin={{ top: 4, right: 8, left: -18 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#DCE6E4" />
            <XAxis dataKey="language" tick={{ fontSize: 11 }} />
            <YAxis tick={{ fontSize: 11 }} allowDecimals={false} />
            <Tooltip />
            <Legend wrapperStyle={{ fontSize: 12 }} />
            <Bar dataKey="whatsapp" name="WhatsApp" stackId="c" fill="#12756B" />
            <Bar dataKey="ussd" name="USSD" stackId="c" fill="#0B3B37" radius={[3, 3, 0, 0]} />
          </BarChart>
        </ResponsiveContainer>
      </div>
      <div className="panel">
        <h4>
          Triage mix per language <span>· emergency rate highlights risk skew</span>
        </h4>
        <table className="tbl">
          <thead>
            <tr>
              <th>Language</th>
              <th className="num">Sessions</th>
              <th className="num">Self-care</th>
              <th className="num">Clinic</th>
              <th className="num">Emergency</th>
              <th className="num">Emergency rate</th>
            </tr>
          </thead>
          <tbody>
            {languages.map((l) => (
              <tr key={l.language}>
                <td>
                  <i
                    className="k"
                    style={{ background: LANG_COLORS[l.language] ?? '#9DB8B4' }}
                  />
                  {l.language}
                </td>
                <td className="num mono">{l.total}</td>
                <td className="num mono">{l.SELF_CARE}</td>
                <td className="num mono">{l.CLINIC}</td>
                <td className="num mono">{l.EMERGENCY}</td>
                <td className="num mono">{Math.round(l.emergency_rate * 100)}%</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  )
}

function FacilityRouting({ facilities, misses }) {
  const missTotal = misses?.total ?? 0
  if (!facilities?.length && !missTotal)
    return (
      <div className="empty">
        No facility referrals yet. These appear when a patient shares their
        location after a clinic or emergency verdict.
      </div>
    )
  return (
    <>
      {missTotal > 0 && (
        <div className="banner-warn" role="status" style={{ marginBottom: 16 }}>
          <b>Routing coverage gap</b>
          <p>
            {missTotal} referral{missTotal === 1 ? '' : 's'} could not be
            routed — no facility was found near the patient's location.
            {misses.by_level?.EMERGENCY
              ? ` ${misses.by_level.EMERGENCY} of them were emergencies.`
              : ''}{' '}
            Coordinates are discarded by design, so only counts are available.
          </p>
        </div>
      )}
      <div className="panel">
        <h4>
          Facilities receiving referrals <span>· distance patients travel</span>
        </h4>
        <table className="tbl">
          <thead>
            <tr>
              <th>Facility</th>
              <th>Type</th>
              <th>LGA, State</th>
              <th className="num">Referrals</th>
              <th className="num">Emergencies</th>
              <th className="num">Avg distance</th>
            </tr>
          </thead>
          <tbody>
            {facilities.length === 0 && (
              <tr>
                <td colSpan={6} className="muted">No successful referrals yet.</td>
              </tr>
            )}
            {facilities.map((f) => (
              <tr key={f.facility}>
                <td>{f.facility}</td>
                <td className="muted">{f.type.replace(/_/g, ' ').toLowerCase()}</td>
                <td className="muted">
                  {f.lga}, {f.state}
                </td>
                <td className="num mono">{f.referrals}</td>
                <td className="num mono" style={{ color: TRIAGE.EMERGENCY.color }}>
                  {f.emergencies}
                </td>
                <td className="num mono">{f.avg_distance_km} km</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  )
}

function KnowledgeBase() {
  const [kb, setKb] = useState(null)
  const [token, setToken] = useState('')
  const [msg, setMsg] = useState(null)
  const [busy, setBusy] = useState(false)
  const [query, setQuery] = useState('child fever danger signs')
  const [hits, setHits] = useState(null)

  const reload = () => getJSON('/api/knowledge').then(setKb)

  useEffect(() => {
    reload().catch((e) => setMsg({ kind: 'err', text: String(e) }))
  }, [])

  // While a rebuild runs, poll so progress is visible instead of the
  // page appearing to hang for several minutes.
  useEffect(() => {
    if (!kb?.rebuild?.running) return
    const t = setInterval(() => reload().catch(() => {}), 3000)
    return () => clearInterval(t)
  }, [kb?.rebuild?.running])

  const post = async (path, fields) => {
    setBusy(true)
    setMsg(null)
    try {
      const body = new FormData()
      body.append('admin_token', token)
      Object.entries(fields).forEach(([k, v]) => body.append(k, v))
      const res = await fetch(path, { method: 'POST', body })
      const json = await res.json().catch(() => ({}))
      if (!res.ok) throw new Error(json.detail || res.status)
      return json
    } finally {
      setBusy(false)
    }
  }

  const upload = async (file) => {
    try {
      const r = await post('/api/knowledge/upload', { file })
      await reload()
      setMsg({
        kind: 'ok',
        text: `${r.replaced ? 'Replaced' : 'Added'} ${r.name} (${r.size_mb} MB). Rebuild the index to make it searchable.`,
      })
    } catch (e) {
      setMsg({ kind: 'err', text: String(e.message ?? e) })
    }
  }

  const remove = async (name) => {
    try {
      await post('/api/knowledge/delete', { name })
      await reload()
      setMsg({ kind: 'ok', text: `Removed ${name}. Rebuild to drop it from retrieval.` })
    } catch (e) {
      setMsg({ kind: 'err', text: String(e.message ?? e) })
    }
  }

  const rebuild = async () => {
    try {
      const r = await post('/api/knowledge/rebuild', {})
      await reload()
      setMsg(
        r.started
          ? { kind: 'ok', text: 'Rebuilding. Retrieval keeps using the current index until it finishes.' }
          : { kind: 'err', text: r.reason }
      )
    } catch (e) {
      setMsg({ kind: 'err', text: String(e.message ?? e) })
    }
  }

  const runPreview = async () => {
    setHits(null)
    try {
      setHits(await getJSON(`/api/knowledge/preview?q=${encodeURIComponent(query)}`))
    } catch (e) {
      setMsg({ kind: 'err', text: String(e) })
    }
  }

  if (!kb) return <div className="empty">Loading…</div>
  const rb = kb.rebuild ?? {}

  return (
    <>
      <div className="panel" style={{ marginBottom: 16 }}>
        <h4>
          Retrieval status <span>· what triage guidance is grounded in</span>
        </h4>
        <table className="tbl">
          <tbody>
            <tr><td>Vector store</td><td className="mono">{kb.store}</td></tr>
            <tr><td>Embeddings</td><td className="mono">{kb.embedding_provider} · {kb.embedding_model}</td></tr>
            <tr>
              <td>Index built with</td>
              <td className="mono">
                {kb.index_embedding_provider
                  ? `${kb.index_embedding_provider} · ${kb.index_embedding_model}`
                  : 'unknown (no sidecar metadata)'}
              </td>
            </tr>
            <tr><td>Documents</td><td className="mono">{kb.documents.length}</td></tr>
            <tr>
              <td>Index</td>
              <td className="mono">
                {kb.index_built_at
                  ? `${kb.index_size_mb} MB · built ${new Date(kb.index_built_at).toLocaleString()}`
                  : 'not built'}
              </td>
            </tr>
          </tbody>
        </table>
        {kb.index_matches_config === false && (
          <div className="banner-warn" role="status" style={{ marginTop: 12 }}>
            <b>{kb.index_embedding_provider ? 'Index / embedding mismatch' : 'Index metadata missing'}</b>
            {kb.index_embedding_provider ? (
              <p>
                The index was built with {kb.index_embedding_provider} (
                {kb.index_embedding_model}) but the current setting is{' '}
                {kb.embedding_provider} ({kb.embedding_model}). Retrieval
                compares queries and stored chunks across different embedding
                spaces — rebuild the index before trusting triage grounding.
              </p>
            ) : (
              <p>
                This index predates embedding metadata. Rebuild it to confirm
                retrieval uses the current embedding model ({kb.embedding_provider},{' '}
                {kb.embedding_model}) — everything else keeps working meanwhile.
              </p>
            )}
          </div>
        )}
        {rb.running && (
          <p className="msg-ok" style={{ marginTop: 12 }}>
            Rebuilding since {new Date(rb.started).toLocaleTimeString()} — this takes
            a few minutes. Retrieval is still serving the previous index.
          </p>
        )}
        {!rb.running && rb.error && <p className="msg-err">Last rebuild failed — {rb.error}</p>}
        {!rb.running && rb.chunks != null && (
          <p className="msg-ok" style={{ marginTop: 12 }}>
            Last rebuild indexed {rb.chunks.toLocaleString()} chunks.
          </p>
        )}
      </div>

      <div className="panel" style={{ marginBottom: 16 }}>
        <h4>
          Protocol documents <span>· PDF, TXT or MD</span>
        </h4>
        <table className="tbl">
          <thead>
            <tr>
              <th>Document</th><th>Type</th><th className="num">Size</th><th>Added</th><th />
            </tr>
          </thead>
          <tbody>
            {kb.documents.length === 0 && (
              <tr><td colSpan={5} className="muted">No documents yet.</td></tr>
            )}
            {kb.documents.map((d) => (
              <tr key={d.name}>
                <td>{d.name}</td>
                <td className="muted">{d.type}</td>
                <td className="num mono">{d.size_mb} MB</td>
                <td className="muted">{new Date(d.modified).toLocaleDateString()}</td>
                <td className="num">
                  <button
                    type="button"
                    className="chip"
                    disabled={busy || !kb.rebuild}
                    onClick={() => remove(d.name)}
                  >
                    Remove
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>

        <label className="f" style={{ marginTop: 16 }}>Admin token</label>
        <input
          className="inp"
          type="password"
          placeholder="ADMIN_TOKEN from .env"
          value={token}
          onChange={(e) => setToken(e.target.value)}
        />
        <div className="row-actions" style={{ marginTop: 12 }}>
          <label className="btn btn-ghost">
            Upload document
            <input
              type="file"
              accept=".pdf,.txt,.md"
              hidden
              onChange={(e) => e.target.files[0] && upload(e.target.files[0])}
            />
          </label>
          <button type="button" className="btn" onClick={rebuild} disabled={busy || rb.running}>
            {rb.running ? 'Rebuilding…' : 'Rebuild index'}
          </button>
        </div>
        <p className="muted" style={{ fontSize: 12, marginTop: 10 }}>
          Uploading changes what triage guidance is grounded in, so it needs the
          admin token. A new document is only searchable after a rebuild.
        </p>
        {msg && <p className={msg.kind === 'ok' ? 'msg-ok' : 'msg-err'}>{msg.text}</p>}
      </div>

      <div className="panel">
        <h4>
          Test retrieval <span>· see what the engine would find</span>
        </h4>
        <div className="row-actions">
          <input
            className="inp"
            style={{ maxWidth: 380 }}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && runPreview()}
            placeholder="e.g. child fever danger signs"
          />
          <button type="button" className="btn" onClick={runPreview}>Search</button>
        </div>
        {hits && hits.length === 0 && (
          <div className="empty">Nothing retrieved — is the index built?</div>
        )}
        {hits?.map((h, i) => (
          <div className="vig" key={i}>
            <div className="vig-head">
              <span className="mono vig-id">
                {h.source}{h.page ? ` · p.${h.page}` : ''}
              </span>
            </div>
            <p style={{ fontSize: 13 }}>{h.text}</p>
          </div>
        ))}
      </div>
    </>
  )
}

function Settings() {
  const [cfg, setCfg] = useState(null)
  const [token, setToken] = useState('')
  const [form, setForm] = useState({})
  const [msg, setMsg] = useState(null)
  const [busy, setBusy] = useState(false)

  const reload = () =>
    getJSON('/api/settings').then((c) => {
      setCfg(c)
      setForm({
        OPENAI_BASE_URL: c.settings.OPENAI_BASE_URL.value,
        OPENAI_MODEL: c.settings.OPENAI_MODEL.value,
        EMBEDDING_PROVIDER: c.settings.EMBEDDING_PROVIDER.value,
        OPENAI_API_KEY: '',
        HF_API_TOKEN: '',
        HF_EMBEDDING_MODEL: c.settings.HF_EMBEDDING_MODEL.value,
      })
    })

  useEffect(() => {
    reload().catch((e) => setMsg({ kind: 'err', text: String(e) }))
  }, [])

  const post = async (path, fields) => {
    setBusy(true)
    setMsg(null)
    try {
      const body = new FormData()
      body.append('admin_token', token)
      Object.entries(fields).forEach(([k, v]) => body.append(k, v))
      const res = await fetch(path, { method: 'POST', body })
      const json = await res.json().catch(() => ({}))
      if (!res.ok) throw new Error(json.detail || res.status)
      return json
    } finally {
      setBusy(false)
    }
  }

  const save = async () => {
    try {
      await post('/api/settings', form)
      await reload()
      setMsg({ kind: 'ok', text: 'Saved. New messages will use these settings.' })
    } catch (e) {
      setMsg({ kind: 'err', text: String(e.message ?? e) })
    }
  }

  const usePreset = async (name) => {
    try {
      await post('/api/settings', { preset: name })
      await reload()
      setMsg({ kind: 'ok', text: `Switched to ${cfg.presets[name].label}. Set the API key if it differs.` })
    } catch (e) {
      setMsg({ kind: 'err', text: String(e.message ?? e) })
    }
  }

  const testIt = async () => {
    try {
      const r = await post('/api/settings/test', {})
      const emb = r.embeddings ?? {}
      const embText = emb.ok
        ? `Embeddings (${emb.provider}): OK${emb.note ? ` — ${emb.note}` : ''}`
        : `Embeddings (${emb.provider}): FAILED — ${emb.error ?? 'unknown error'}`
      setMsg(
        r.ok
          ? { kind: 'ok', text: `${r.model} replied: ${r.sample} · ${embText}` }
          : { kind: 'err', text: `${r.model} failed — ${r.error} · ${embText}` }
      )
    } catch (e) {
      setMsg({ kind: 'err', text: String(e.message ?? e) })
    }
  }

  if (!cfg) return <div className="empty">Loading…</div>

  const preset = cfg.presets[cfg.provider]

  return (
    <>
      {!cfg.writes_enabled && (
        <div className="banner-warn" role="status">
          <b>Read-only</b>
          <p>{cfg.note}</p>
        </div>
      )}

      <div className="panel" style={{ marginBottom: 16 }}>
        <h4>
          Current provider <span>· used by every new message</span>
        </h4>
        <table className="tbl">
          <tbody>
            {Object.entries(cfg.settings).map(([key, s]) => (
              <tr key={key}>
                <td>{s.label}</td>
                <td className="mono">
                  {s.secret
                    ? s.is_set
                      ? `set (${s.value})`
                      : 'not set'
                    : s.value || '(blank — OpenAI direct)'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {preset?.note && (
          <p className="muted" style={{ fontSize: 12, marginTop: 10 }}>
            {preset.note}
          </p>
        )}
        <div className="row-actions" style={{ marginTop: 14 }}>
          <button type="button" className="btn" onClick={testIt} disabled={busy || !cfg.writes_enabled}>
            Test chat + embeddings
          </button>
        </div>
      </div>

      <div className="panel" style={{ marginBottom: 16 }}>
        <h4>Quick switch</h4>
        <div className="row-actions">
          {Object.entries(cfg.presets).map(([name, p]) => (
            <button
              key={name}
              type="button"
              className={`chip${cfg.provider === name ? ' chip-on' : ''}`}
              disabled={busy || !cfg.writes_enabled}
              onClick={() => usePreset(name)}
            >
              {p.label}
            </button>
          ))}
        </div>
        <p className="muted" style={{ fontSize: 12, marginTop: 10 }}>
          Presets set the base URL, model and embedding provider together so
          they cannot drift apart. Gateways without an embedding API fall back
          to on-device embeddings — if the knowledge index was built with
          another embedding model, rebuild it on the Knowledge base page.
        </p>
      </div>

      <div className="panel">
        <h4>Change settings</h4>

        <label className="f">Admin token</label>
        <input
          className="inp"
          type="password"
          placeholder="ADMIN_TOKEN from .env"
          value={token}
          onChange={(e) => setToken(e.target.value)}
          disabled={!cfg.writes_enabled}
        />

        <label className="f" style={{ marginTop: 12 }}>
          API key {cfg.settings.OPENAI_API_KEY.is_set && '(leave blank to keep the current one)'}
        </label>
        <input
          className="inp"
          type="password"
          placeholder={preset?.key_hint ?? 'sk-…'}
          value={form.OPENAI_API_KEY}
          onChange={(e) => setForm({ ...form, OPENAI_API_KEY: e.target.value })}
          disabled={!cfg.writes_enabled}
        />

        <label className="f" style={{ marginTop: 12 }}>Model</label>
        {preset?.models ? (
          <select
            className="inp"
            value={form.OPENAI_MODEL}
            onChange={(e) => setForm({ ...form, OPENAI_MODEL: e.target.value })}
            disabled={!cfg.writes_enabled}
          >
            {[...new Set([...preset.models, form.OPENAI_MODEL].filter(Boolean))].map((m) => (
              <option key={m} value={m}>{m}</option>
            ))}
          </select>
        ) : (
          <input
            className="inp"
            value={form.OPENAI_MODEL}
            onChange={(e) => setForm({ ...form, OPENAI_MODEL: e.target.value })}
            disabled={!cfg.writes_enabled}
          />
        )}

        <label className="f" style={{ marginTop: 12 }}>Base URL</label>
        <input
          className="inp"
          placeholder="blank = OpenAI direct"
          value={form.OPENAI_BASE_URL}
          onChange={(e) => setForm({ ...form, OPENAI_BASE_URL: e.target.value })}
          disabled={!cfg.writes_enabled}
        />

        <label className="f" style={{ marginTop: 12 }}>Embeddings</label>
        <select
          className="inp"
          value={form.EMBEDDING_PROVIDER}
          onChange={(e) => setForm({ ...form, EMBEDDING_PROVIDER: e.target.value })}
          disabled={!cfg.writes_enabled}
        >
          <option value="local">local (on-device, free — use when the provider has no embeddings)</option>
          <option value="openai">openai (API embeddings)</option>
          <option value="hf">hf (Hugging Face hosted — cloud deployments)</option>
        </select>

        <label className="f" style={{ marginTop: 12 }}>
          HF API token {cfg.settings.HF_API_TOKEN.is_set && '(leave blank to keep the current one)'}
        </label>
        <input
          className="inp"
          type="password"
          placeholder="hf_…"
          value={form.HF_API_TOKEN}
          onChange={(e) => setForm({ ...form, HF_API_TOKEN: e.target.value })}
          disabled={!cfg.writes_enabled}
        />

        <label className="f" style={{ marginTop: 12 }}>HF embedding model</label>
        <input
          className="inp"
          value={form.HF_EMBEDDING_MODEL}
          onChange={(e) => setForm({ ...form, HF_EMBEDDING_MODEL: e.target.value })}
          disabled={!cfg.writes_enabled}
        />

        <div className="row-actions" style={{ marginTop: 16 }}>
          <button type="button" className="btn" onClick={save} disabled={busy || !cfg.writes_enabled}>
            Save settings
          </button>
        </div>

        {msg && (
          <p className={msg.kind === 'ok' ? 'msg-ok' : 'msg-err'}>{msg.text}</p>
        )}
        <p className="muted" style={{ fontSize: 12, marginTop: 12 }}>
          Keys are stored in the database and never sent back to this page —
          only the last four characters are shown.
        </p>
      </div>
    </>
  )
}

function SusStudy() {
  const [data, setData] = useState(null)
  const [err, setErr] = useState(null)

  useEffect(() => {
    getJSON('/api/sus/summary').then(setData).catch((e) => setErr(String(e)))
  }, [])

  if (err) return <div className="empty">Could not load: {err}</div>
  if (!data) return <div className="empty">Loading…</div>

  const surveyUrl = `${window.location.origin}/survey`

  return (
    <>
      <div className="panel" style={{ marginBottom: 16 }}>
        <h4>
          Participant survey link <span>· share this with participants</span>
        </h4>
        <div className="row-actions">
          <a className="btn" href="/survey" target="_blank" rel="noreferrer">
            Open survey
          </a>
          <code className="mono surveylink">{surveyUrl}</code>
        </div>
        <p className="muted" style={{ fontSize: 12, marginTop: 10 }}>
          Give each participant a code (P01, P02…). No names or phone
          numbers are collected.
        </p>
      </div>

      {data.n === 0 ? (
        <div className="empty">
          No responses yet. Results appear here as participants submit.
        </div>
      ) : (
        <>
          <div className="kpis" style={{ marginBottom: 16 }}>
            <div className="kpi">
              <div className="lab">Mean SUS</div>
              <div className="val">{data.mean}</div>
              <div className={`d ${data.meets_target ? '' : 'warn'}`}>
                {data.meets_target ? '▲' : '▼'} target &gt; {data.target}
              </div>
            </div>
            <div className="kpi">
              <div className="lab">Participants</div>
              <div className="val">{data.n}</div>
              <div className="d">{data.n >= 20 ? 'target met' : `${20 - data.n} more for 20`}</div>
            </div>
            <div className="kpi">
              <div className="lab">Grade</div>
              <div className="val" style={{ fontSize: 19 }}>{data.grade}</div>
              <div className="d">Sauro &amp; Lewis curve</div>
            </div>
            <div className="kpi">
              <div className="lab">Spread</div>
              <div className="val mono" style={{ fontSize: 19 }}>
                {data.min}–{data.max}
              </div>
              <div className="d">SD {data.std_dev} · median {data.median}</div>
            </div>
          </div>

          <div className="grid2" style={{ marginBottom: 16 }}>
            <div className="panel">
              <h4>
                Score per participant <span>· dashed line = target 68</span>
              </h4>
              <ResponsiveContainer width="100%" height={260}>
                <BarChart
                  data={data.responses.map((r) => ({
                    name: r.participant_code,
                    score: r.score,
                  }))}
                  margin={{ top: 4, right: 8, left: -18 }}
                >
                  <CartesianGrid strokeDasharray="3 3" stroke="#DCE6E4" />
                  <XAxis dataKey="name" tick={{ fontSize: 11 }} />
                  <YAxis domain={[0, 100]} tick={{ fontSize: 11 }} />
                  <Tooltip />
                  <ReferenceLine y={68} stroke="#B45309" strokeDasharray="4 4" />
                  <Bar dataKey="score" name="SUS score" fill="#12756B" radius={[3, 3, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </div>
            <div className="panel">
              <h4>By language and channel</h4>
              <table className="tbl">
                <thead>
                  <tr>
                    <th>Group</th>
                    <th className="num">n</th>
                    <th className="num">Mean SUS</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(data.by_language).map(([k, v]) => (
                    <tr key={`l-${k}`}>
                      <td style={{ textTransform: 'capitalize' }}>{k}</td>
                      <td className="num mono">{v.n}</td>
                      <td className="num mono">{v.mean}</td>
                    </tr>
                  ))}
                  {Object.entries(data.by_channel).map(([k, v]) => (
                    <tr key={`c-${k}`}>
                      <td className="muted">{k}</td>
                      <td className="num mono">{v.n}</td>
                      <td className="num mono">{v.mean}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          <div className="panel" style={{ marginBottom: 16 }}>
            <h4>
              Per-item means <span>· which aspects scored weakest</span>
            </h4>
            <table className="tbl">
              <thead>
                <tr>
                  <th>#</th>
                  <th>Statement</th>
                  <th className="num">Mean (1–5)</th>
                  <th className="num">Reads as</th>
                </tr>
              </thead>
              <tbody>
                {data.item_means.map((it) => {
                  // Odd items are positive (high = good); even items are
                  // negatively worded, so a high mean is bad.
                  const good = it.positive ? it.mean >= 3.5 : it.mean <= 2.5
                  return (
                    <tr key={it.item}>
                      <td className="mono">{it.item}</td>
                      <td style={{ fontSize: 12.5 }}>{it.text}</td>
                      <td className="num mono">{it.mean}</td>
                      <td
                        className="num"
                        style={{ color: good ? '#2E7D32' : '#B45309', fontSize: 12 }}
                      >
                        {good ? 'favourable' : 'needs attention'}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>

          <div className="panel">
            <h4>Responses</h4>
            <div className="row-actions" style={{ marginBottom: 12 }}>
              <a className="btn" href="/api/export/sus.csv" download>
                Download responses CSV
              </a>
            </div>
            <table className="tbl">
              <thead>
                <tr>
                  <th>Participant</th>
                  <th>Language</th>
                  <th>Channel</th>
                  <th className="num">Score</th>
                  <th>Comment</th>
                </tr>
              </thead>
              <tbody>
                {data.responses.map((r) => (
                  <tr key={r.participant_code + r.created_at}>
                    <td className="mono">{r.participant_code}</td>
                    <td className="muted" style={{ textTransform: 'capitalize' }}>
                      {r.language}
                    </td>
                    <td className="muted">{r.channel}</td>
                    <td className="num mono">{r.score}</td>
                    <td className="muted" style={{ fontSize: 12 }}>
                      {r.comments || '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </>
  )
}

function ClinicalValidation() {
  const [items, setItems] = useState(null)
  const [prog, setProg] = useState(null)
  const [validator, setValidator] = useState(
    () => localStorage.getItem('healthbot.validator') ?? ''
  )
  const [filter, setFilter] = useState('pending')
  const [busy, setBusy] = useState(null)
  const [msg, setMsg] = useState(null)

  const reload = () =>
    Promise.all([getJSON('/api/vignettes'), getJSON('/api/vignettes/progress')])
      .then(([v, p]) => {
        setItems(v)
        setProg(p)
      })
      .catch((e) => setMsg(String(e)))

  useEffect(() => {
    reload()
  }, [])

  const saveValidator = (name) => {
    setValidator(name)
    localStorage.setItem('healthbot.validator', name)
  }

  const submit = async (vignetteId, level, notes) => {
    if (!validator.trim()) {
      setMsg('Enter the validating clinician’s name first — the record must say who decided.')
      return
    }
    setBusy(vignetteId)
    setMsg(null)
    try {
      const body = new FormData()
      body.append('level', level)
      body.append('validated_by', validator)
      body.append('notes', notes ?? '')
      const res = await fetch(`/api/vignettes/${vignetteId}/validate`, { method: 'POST', body })
      if (!res.ok) throw new Error(`${res.status}`)
      await reload()
    } catch (e) {
      setMsg(`Could not save: ${e}`)
    } finally {
      setBusy(null)
    }
  }

  const upload = async (file) => {
    setMsg(null)
    const body = new FormData()
    body.append('file', file)
    const res = await fetch('/api/vignettes/import', { method: 'POST', body })
    const json = await res.json().catch(() => ({}))
    if (!res.ok) {
      setMsg(json.detail ?? 'Import failed')
      return
    }
    setMsg(`Imported ${json.total} vignettes (${json.added} new, ${json.updated} updated).`)
    reload()
  }

  if (!items) return <div className="empty">Loading vignettes…</div>

  const shown = items.filter((v) =>
    filter === 'all'
      ? true
      : filter === 'pending'
        ? v.validations.length === 0
        : filter === 'disputed'
          ? v.disputed
          : filter === 'changed'
            ? v.agrees === false
            : !!v.consensus_level
  )

  return (
    <>
      <div className="panel" style={{ marginBottom: 16 }}>
        <h4>
          Label-review progress{' '}
          <span>· researcher-assigned draft levels reviewed by a clinician</span>
        </h4>
        {prog && (
          <>
            <div className="kpis" style={{ marginBottom: 12 }}>
              <div className="kpi">
                <div className="lab">Reviewed</div>
                <div className="val">
                  {prog.validated}/{prog.total}
                </div>
                <div className="d">{prog.pending} pending</div>
              </div>
              <div className="kpi">
                <div className="lab">Draft agreed</div>
                <div className="val">{prog.agreed}</div>
                <div className="d">drafted label confirmed</div>
              </div>
              <div className="kpi">
                <div className="lab">Corrected</div>
                <div className="val">{prog.changed}</div>
                <div className="d">label changed by clinician</div>
              </div>
              <div className="kpi">
                <div className="lab">Observed agreement</div>
                <div className="val">
                  {prog.agreement_rate === null
                    ? '—'
                    : `${Math.round(prog.agreement_rate * 100)}%`}
                </div>
                <div className="d">descriptive review-workflow result</div>
              </div>
            </div>
            <div className="bar" style={{ height: 10 }}>
              <i
                style={{
                  width: `${prog.total ? (prog.validated / prog.total) * 100 : 0}%`,
                  background: '#12756B',
                }}
              />
            </div>
            {prog.validators.length > 0 && (
              <p className="muted" style={{ fontSize: 12, marginTop: 10 }}>
                Raters:{' '}
                {prog.validators
                  .map((v) => `${v} (${prog.per_validator[v]})`)
                  .join(' · ')}
                {prog.disputed > 0 && ` — ${prog.disputed} disputed, need adjudication`}
              </p>
            )}
            {prog.pairwise_kappa.length > 0 && (
              <table className="tbl" style={{ marginTop: 14 }}>
                <thead>
                  <tr>
                    <th>Inter-rater reliability</th>
                    <th className="num">Both rated</th>
                    <th className="num">Raw agreement</th>
                    <th className="num">Cohen&rsquo;s &kappa;</th>
                    <th>Interpretation</th>
                  </tr>
                </thead>
                <tbody>
                  {prog.pairwise_kappa.map((k) => (
                    <tr key={k.raters.join('|')}>
                      <td>{k.raters.join(' vs ')}</td>
                      <td className="num mono">{k.n}</td>
                      <td className="num mono">
                        {k.raw_agreement === null
                          ? '—'
                          : `${Math.round(k.raw_agreement * 100)}%`}
                      </td>
                      <td className="num mono">{k.kappa === null ? '—' : k.kappa}</td>
                      <td className="muted">{k.interpretation ?? 'needs 2+ shared items'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </>
        )}
      </div>

      <div className="panel" style={{ marginBottom: 16 }}>
        <h4>Reviewing clinician</h4>
        <input
          className="inp"
          placeholder="e.g. Dr Emmanuel Mkpojiogu"
          value={validator}
          onChange={(e) => saveValidator(e.target.value)}
        />
        <p className="muted" style={{ fontSize: 12, marginTop: 8 }}>
          Recorded against each reviewed draft level with a timestamp. Submitted
          reviews require documented provenance before they are treated as evidence.
        </p>
        <div className="row-actions" style={{ marginTop: 14 }}>
          <label className="btn btn-ghost">
            Import vignettes CSV
            <input
              type="file"
              accept=".csv"
              hidden
              onChange={(e) => e.target.files[0] && upload(e.target.files[0])}
            />
          </label>
          <a className="btn" href="/api/vignettes/export.csv" download>
            Export review CSV
          </a>
        </div>
        {msg && (
          <p className="muted" style={{ fontSize: 12, marginTop: 10 }}>
            {msg}
          </p>
        )}
      </div>

      <div className="panel">
        <h4>
          Vignettes <span>· {shown.length} shown</span>
        </h4>
        <div className="row-actions" style={{ marginBottom: 12 }}>
          {['pending', 'validated', 'disputed', 'changed', 'all'].map((f) => (
            <button
              key={f}
              type="button"
              className={`chip${filter === f ? ' chip-on' : ''}`}
              onClick={() => setFilter(f)}
            >
              {f === 'validated' ? 'reviewed' : f}
            </button>
          ))}
        </div>
        {shown.length === 0 && <div className="empty">Nothing here.</div>}
        {shown.map((v) => (
          <VignetteCard
            key={v.vignette_id}
            v={v}
            busy={busy === v.vignette_id}
            onSubmit={submit}
            myName={validator}
          />
        ))}
      </div>
    </>
  )
}

function VignetteCard({ v, busy, onSubmit, myName }) {
  const [notes, setNotes] = useState(v.notes ?? '')
  return (
    <div className="vig">
      <div className="vig-head">
        <span className="mono vig-id">{v.vignette_id}</span>
        <span className="chip chip-lang">{v.language}</span>
        {v.disputed ? (
          <span className="badge b-clinic" title="Raters disagree — needs adjudication">
            DISPUTED
          </span>
        ) : v.consensus_level ? (
          <span className={`badge ${TRIAGE[v.consensus_level]?.badge ?? ''}`}>
            {v.agrees ? 'CONFIRMED' : 'CORRECTED'} · {TRIAGE[v.consensus_level]?.label}
          </span>
        ) : (
          <span className="chip">drafted: {TRIAGE[v.proposed_level]?.label}</span>
        )}
      </div>
      <ol className="vig-msgs">
        {v.messages.map((m, i) => (
          <li key={i}>{m}</li>
        ))}
      </ol>
      <div className="row-actions">
        {Object.entries(TRIAGE).map(([level, t]) => {
          const mine = v.validations.find((x) => x.validator === myName)
          const active = (mine?.level ?? v.consensus_level ?? v.proposed_level) === level
          return (
            <button
              key={level}
              type="button"
              disabled={busy}
              className="chip"
              style={
                active
                  ? { background: `${t.color}1a`, color: t.color, borderColor: t.color }
                  : undefined
              }
              onClick={() => onSubmit(v.vignette_id, level, notes)}
            >
              {t.label}
            </button>
          )
        })}
        <input
          className="inp inp-sm"
          placeholder="Clinical note (optional)"
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
        />
      </div>
      {v.validations.length > 0 && (
        <p className="muted vig-meta">
          {v.validations
            .map(
              (x) =>
                `${x.validator}: ${TRIAGE[x.level]?.label ?? x.level}` +
                (x.notes ? ` (“${x.notes}”)` : '')
            )
            .join(' · ')}
          {v.disputed && ' — raters disagree, needs adjudication'}
        </p>
      )}
    </div>
  )
}

function DataExport({ summary }) {
  return (
    <div className="panel">
      <h4>Export pseudonymised triage records</h4>
      <p className="muted" style={{ fontSize: 13, marginBottom: 14 }}>
        Every row is a completed triage decision. Sessions use a linkable one-way
        hash. The default triage record excludes direct identifiers, phone numbers
        and coordinates, but the hash remains pseudonymous rather than anonymous.
      </p>
      <table className="tbl" style={{ marginBottom: 16 }}>
        <tbody>
          <tr>
            <td>Records available</td>
            <td className="num mono">{summary.total_sessions}</td>
          </tr>
          <tr>
            <td>Columns</td>
            <td className="muted">
              session_id, created_at, channel, language, triage_level,
              symptom_category, reason
            </td>
          </tr>
          <tr>
            <td>Format</td>
            <td className="muted">CSV (UTF-8)</td>
          </tr>
        </tbody>
      </table>
      <a className="btn" href="/api/export/triage.csv" download>
        Download CSV
      </a>
    </div>
  )
}

function Kpis({ summary }) {
  const selfCare = summary.by_level.SELF_CARE ?? 0
  const ussdPct = Math.round(summary.ussd_share * 100)
  return (
    <div className="kpis">
      <div className="kpi">
        <div className="lab">Triage sessions</div>
        <div className="val">{summary.total_sessions.toLocaleString()}</div>
        <div className="d">all channels</div>
      </div>
      <div className="kpi">
        <div className="lab">Via USSD (no mobile data)</div>
        <div className="val">{(summary.by_channel.ussd ?? 0).toLocaleString()}</div>
        <div className="d">{ussdPct}% of all traffic</div>
      </div>
      <div className="kpi">
        <div className="lab">Emergencies routed</div>
        <div className="val">{summary.emergencies.toLocaleString()}</div>
        <div className="d warn">▲ needs attention</div>
      </div>
      <div className="kpi">
        <div className="lab">Resolved with self-care</div>
        <div className="val">{selfCare.toLocaleString()}</div>
        <div className="d">managed at home</div>
      </div>
    </div>
  )
}

function TriageLanes({ byLevel }) {
  const max = Math.max(1, ...Object.values(byLevel))
  return (
    <div className="panel lanes">
      <h4>
        Triage outcomes <span>· all time, all channels</span>
      </h4>
      {Object.entries(TRIAGE).map(([level, t]) => {
        const n = byLevel[level] ?? 0
        return (
          <div className="lane" key={level}>
            <span
              className="tag"
              style={{ background: `${t.color}1a`, color: t.color }}
            >
              {t.label}
            </span>
            <div className="bar">
              <i style={{ width: `${(n / max) * 100}%`, background: t.color }} />
            </div>
            <span className="n">{n.toLocaleString()}</span>
          </div>
        )
      })}
    </div>
  )
}

function DailyChart({ daily }) {
  const data = daily.map((d) => ({ ...d, day: d.date.slice(5) }))
  return (
    <ResponsiveContainer width="100%" height={260}>
      <BarChart data={data} margin={{ top: 4, right: 8, left: -18, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#DCE6E4" />
        <XAxis dataKey="day" tick={{ fontSize: 11 }} />
        <YAxis tick={{ fontSize: 11 }} allowDecimals={false} />
        <Tooltip />
        <Legend wrapperStyle={{ fontSize: 12 }} />
        <Bar dataKey="SELF_CARE" name="Self-care" stackId="a" fill={TRIAGE.SELF_CARE.color} />
        <Bar dataKey="CLINIC" name="Clinic" stackId="a" fill={TRIAGE.CLINIC.color} />
        <Bar dataKey="EMERGENCY" name="Emergency" stackId="a" fill={TRIAGE.EMERGENCY.color} radius={[3, 3, 0, 0]} />
      </BarChart>
    </ResponsiveContainer>
  )
}

function LanguageMix({ byLanguage }) {
  const total = Object.values(byLanguage).reduce((a, b) => a + b, 0)
  if (!total) return <div className="empty">No sessions yet.</div>
  const langs = Object.entries(byLanguage).sort((a, b) => b[1] - a[1])
  return (
    <>
      <div className="langbar">
        {langs.map(([lang, n]) => (
          <i
            key={lang}
            title={lang}
            style={{
              width: `${(n / total) * 100}%`,
              background: LANG_COLORS[lang] ?? '#9DB8B4',
            }}
          />
        ))}
      </div>
      <div className="legend">
        {langs.map(([lang, n]) => (
          <span key={lang}>
            <i className="k" style={{ background: LANG_COLORS[lang] ?? '#9DB8B4' }} />
            <b>{lang[0].toUpperCase() + lang.slice(1)}</b>{' '}
            {Math.round((n / total) * 100)}%
          </span>
        ))}
      </div>
    </>
  )
}

function RecentCases({ recent }) {
  if (!recent.length) return <div className="empty">No cases yet.</div>
  return recent.map((c, i) => {
    const t = TRIAGE[c.level] ?? TRIAGE.CLINIC
    return (
      <div className="case" key={i}>
        <span className={`badge ${t.badge}`}>{t.label.split(' ')[0]}</span>
        <div>
          {c.reason || '—'}
          <small>
            {c.language} · {c.channel}
          </small>
        </div>
        <span className="ago">{c.minutes_ago}m</span>
      </div>
    )
  })
}
