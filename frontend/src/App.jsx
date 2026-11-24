import React, { useEffect, useMemo, useState } from 'react'
import { Activity, BarChart3, DoorOpen, LogOut, Shield, Users, Video } from 'lucide-react'

async function api(path, options = {}) {
  const res = await fetch(path, {
    credentials: 'include',
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    ...options,
  })
  if (!res.ok) {
    const text = await res.text()
    throw new Error(text || res.statusText)
  }
  const type = res.headers.get('content-type') || ''
  if (type.includes('application/json')) return res.json()
  return res
}

function Login({ onOk }) {
  const [code, setCode] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  const submit = async (e) => {
    e.preventDefault()
    setBusy(true)
    setError('')
    try {
      await api('/api/auth/login', { method: 'POST', body: JSON.stringify({ code }) })
      onOk()
    } catch (err) {
      setError('登录失败：验证码错误或会话已满')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="min-h-full grid place-items-center p-6">
      <form onSubmit={submit} className="w-full max-w-sm bg-[var(--panel)] border border-[#cfd8dd] p-8 shadow-[0_20px_60px_rgba(26,31,36,0.08)]">
        <p className="text-xs tracking-[0.2em] uppercase text-[var(--muted)] mb-2">cctv.lobn</p>
        <h1 className="text-3xl font-semibold tracking-tight mb-1">SmartCCTV</h1>
        <p className="text-sm text-[var(--muted)] mb-8">内网监控 · TOTP 登录</p>
        <label className="block text-sm mb-2">验证码</label>
        <input
          className="mono w-full border border-[#cfd8dd] bg-white px-3 py-3 text-2xl tracking-[0.4em] outline-none focus:border-[var(--accent)]"
          value={code}
          onChange={(e) => setCode(e.target.value.replace(/\D/g, '').slice(0, 6))}
          inputMode="numeric"
          autoFocus
          placeholder="000000"
        />
        {error && <p className="text-sm text-[var(--crit)] mt-3">{error}</p>}
        <button
          disabled={busy || code.length < 6}
          className="mt-6 w-full bg-[var(--accent)] text-white py-3 font-medium disabled:opacity-40"
        >
          {busy ? '校验中…' : '进入'}
        </button>
      </form>
    </div>
  )
}

function Dashboard() {
  const [cameras, setCameras] = useState([])
  const [events, setEvents] = useState([])
  const [health, setHealth] = useState(null)
  const [persons, setPersons] = useState([])
  const [stats, setStats] = useState(null)
  const [doorLogs, setDoorLogs] = useState([])
  const [newName, setNewName] = useState('')
  const [tab, setTab] = useState('live')

  const refresh = async () => {
    const [cams, ev, h, p, s, d] = await Promise.all([
      api('/api/cameras'),
      api('/api/events?limit=30'),
      api('/api/system/health'),
      api('/api/faces/persons'),
      api('/api/stats?hours=24'),
      api('/api/door/logs?limit=20'),
    ])
    setCameras(cams)
    setEvents(ev)
    setHealth(h)
    setPersons(p)
    setStats(s)
    setDoorLogs(d)
  }

  const addPerson = async (e) => {
    e.preventDefault()
    if (!newName.trim()) return
    await api('/api/faces/persons', { method: 'POST', body: JSON.stringify({ name: newName.trim() }) })
    setNewName('')
    await refresh()
  }

  const uploadPhoto = async (personId, file) => {
    const body = new FormData()
    body.append('file', file)
    const res = await fetch(`/api/faces/persons/${personId}/photos`, { method: 'POST', credentials: 'include', body })
    if (!res.ok) throw new Error(await res.text())
    await refresh()
  }

  useEffect(() => {
    refresh().catch(console.error)
    const es = new EventSource('/api/events/stream', { withCredentials: true })
    es.onmessage = (msg) => {
      try {
        const data = JSON.parse(msg.data)
        if (data.type && data.type !== 'hello') {
          setEvents((prev) => [{ ...data, id: `${data.ts}-${data.type}` }, ...prev].slice(0, 50))
        }
      } catch (_) {}
    }
    const timer = setInterval(() => refresh().catch(() => {}), 15000)
    return () => {
      es.close()
      clearInterval(timer)
    }
  }, [])

  const statusColor = useMemo(() => {
    if (!health) return 'var(--muted)'
    if (health.status === 'fail') return 'var(--crit)'
    if (health.status === 'warn') return 'var(--warn)'
    return 'var(--accent)'
  }, [health])

  const logout = async () => {
    await api('/api/auth/logout', { method: 'POST' })
    window.location.reload()
  }

  const openDoor = async () => {
    await api('/api/door/open', { method: 'POST' })
    await refresh()
  }

  return (
    <div className="min-h-full flex flex-col">
      <header className="flex items-center justify-between px-6 py-4 border-b border-[#d5dee3] bg-white/70 backdrop-blur">
        <div>
          <div className="text-xs tracking-[0.18em] uppercase text-[var(--muted)]">cctv.lobn</div>
          <div className="text-xl font-semibold">SmartCCTV</div>
        </div>
        <div className="flex items-center gap-3">
          <span className="mono text-xs px-2 py-1 border border-[#d5dee3]" style={{ color: statusColor }}>
            health:{health?.status || '…'}
          </span>
          <button onClick={openDoor} className="inline-flex items-center gap-2 px-3 py-2 bg-[var(--ink)] text-white text-sm">
            <DoorOpen size={16} /> 手动开门
          </button>
          <button onClick={logout} className="inline-flex items-center gap-2 px-3 py-2 border border-[#cfd8dd] text-sm">
            <LogOut size={16} /> 退出
          </button>
        </div>
      </header>

      <nav className="px-6 py-3 flex gap-2 text-sm">
        {[
          ['live', '实况', Video],
          ['events', '事件', Activity],
          ['faces', '人脸库', Users],
          ['stats', '统计', BarChart3],
          ['health', '自检', Shield],
        ].map(([id, label, Icon]) => (
          <button
            key={id}
            onClick={() => setTab(id)}
            className={`inline-flex items-center gap-2 px-3 py-2 border ${tab === id ? 'border-[var(--accent)] bg-white' : 'border-transparent text-[var(--muted)]'}`}
          >
            <Icon size={16} /> {label}
          </button>
        ))}
      </nav>

      <main className="flex-1 px-6 pb-8">
        {tab === 'live' && (
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
            {cameras.length === 0 && (
              <div className="border border-dashed border-[#c5d0d6] p-8 text-[var(--muted)]">
                尚未配置摄像头。编辑 config.yaml 后重启服务。可用 file:///path/to.mp4 联调。
              </div>
            )}
            {cameras.map((cam) => (
              <section key={cam.id} className="bg-white border border-[#d5dee3] overflow-hidden">
                <div className="flex items-center justify-between px-3 py-2 border-b border-[#e6ecef] text-sm">
                  <span className="font-medium">{cam.name}</span>
                  <span className="mono text-xs text-[var(--muted)]">
                    {cam.alive ? 'live' : 'down'} · {cam.mode} · {cam.width}x{cam.height}
                  </span>
                </div>
                <div className="bg-[#101418] aspect-video">
                  {cam.enabled ? (
                    <img
                      src={`/api/cameras/${cam.id}/mjpeg`}
                      alt={cam.name}
                      className="w-full h-full object-contain"
                    />
                  ) : (
                    <div className="h-full grid place-items-center text-white/50">disabled</div>
                  )}
                </div>
              </section>
            ))}
          </div>
        )}

        {tab === 'events' && (
          <div className="bg-white border border-[#d5dee3]">
            <div className="px-4 py-3 border-b border-[#e6ecef] font-medium">最近事件</div>
            <ul className="divide-y divide-[#eef2f4]">
              {events.map((ev) => (
                <li key={ev.id || `${ev.ts}-${ev.type}`} className="px-4 py-3 flex gap-4 text-sm">
                  <span className="mono text-[var(--muted)] w-40 shrink-0">
                    {new Date((ev.ts || 0) * 1000).toLocaleString()}
                  </span>
                  <span className="w-28 shrink-0">{ev.cam_id || '-'}</span>
                  <span className="font-medium w-32 shrink-0">{ev.type}</span>
                  <span className="text-[var(--muted)] truncate">{ev.level}</span>
                </li>
              ))}
              {events.length === 0 && <li className="px-4 py-8 text-[var(--muted)]">暂无事件</li>}
            </ul>
          </div>
        )}

        {tab === 'faces' && (
          <div className="grid gap-4 lg:grid-cols-2">
            <section className="bg-white border border-[#d5dee3]">
              <div className="px-4 py-3 border-b border-[#e6ecef] font-medium">人员</div>
              <form onSubmit={addPerson} className="p-4 flex gap-2 border-b border-[#eef2f4]">
                <input
                  className="flex-1 border border-[#cfd8dd] px-3 py-2"
                  placeholder="姓名"
                  value={newName}
                  onChange={(e) => setNewName(e.target.value)}
                />
                <button className="px-4 py-2 bg-[var(--accent)] text-white">添加</button>
              </form>
              <ul className="divide-y divide-[#eef2f4]">
                {persons.map((p) => (
                  <li key={p.id} className="px-4 py-3 flex items-center justify-between gap-3 text-sm">
                    <div>
                      <div className="font-medium">{p.name}</div>
                      <div className="mono text-xs text-[var(--muted)]">#{p.id}</div>
                    </div>
                    <label className="cursor-pointer border border-[#cfd8dd] px-3 py-1.5">
                      上传照片
                      <input
                        type="file"
                        accept="image/*"
                        className="hidden"
                        onChange={(e) => e.target.files?.[0] && uploadPhoto(p.id, e.target.files[0]).catch((err) => alert(err.message))}
                      />
                    </label>
                  </li>
                ))}
                {persons.length === 0 && <li className="px-4 py-8 text-[var(--muted)]">暂无人员。需先下载 buffalo_l 模型才能注册人脸。</li>}
              </ul>
            </section>
            <section className="bg-white border border-[#d5dee3]">
              <div className="px-4 py-3 border-b border-[#e6ecef] font-medium">门禁日志</div>
              <ul className="divide-y divide-[#eef2f4]">
                {doorLogs.map((row) => (
                  <li key={row.id} className="px-4 py-3 text-sm flex gap-3">
                    <span className="mono text-[var(--muted)] w-40 shrink-0">{new Date(row.ts * 1000).toLocaleString()}</span>
                    <span className="w-24">{row.action}</span>
                    <span className="truncate">{row.person_name || row.reason}</span>
                  </li>
                ))}
                {doorLogs.length === 0 && <li className="px-4 py-8 text-[var(--muted)]">暂无门禁记录</li>}
              </ul>
            </section>
          </div>
        )}

        {tab === 'stats' && (
          <div className="bg-white border border-[#d5dee3]">
            <div className="px-4 py-3 border-b border-[#e6ecef] font-medium">24h 人数统计</div>
            <div className="p-4 grid grid-cols-3 gap-4 text-sm">
              <div className="border border-[#e6ecef] p-4">
                <div className="text-[var(--muted)]">峰值人数</div>
                <div className="text-3xl font-semibold mt-2">{stats?.peaks?.max_persons ?? 0}</div>
              </div>
              <div className="border border-[#e6ecef] p-4">
                <div className="text-[var(--muted)]">事件数</div>
                <div className="text-3xl font-semibold mt-2">{stats?.peaks?.events ?? 0}</div>
              </div>
              <div className="border border-[#e6ecef] p-4">
                <div className="text-[var(--muted)]">分钟桶</div>
                <div className="text-3xl font-semibold mt-2">{stats?.series?.length ?? 0}</div>
              </div>
            </div>
            <ul className="divide-y divide-[#eef2f4] max-h-96 overflow-auto">
              {(stats?.series || []).slice(-40).map((row) => (
                <li key={`${row.cam_id}-${row.bucket_start}`} className="px-4 py-2 text-sm flex gap-4 mono">
                  <span className="w-44 text-[var(--muted)]">{new Date(row.bucket_start * 1000).toLocaleString()}</span>
                  <span className="w-24">{row.cam_id}</span>
                  <span>max {row.person_max}</span>
                  <span>avg {row.person_avg}</span>
                  <span>events {row.events}</span>
                </li>
              ))}
              {!(stats?.series || []).length && <li className="px-4 py-8 text-[var(--muted)]">暂无统计（启动检测后按分钟聚合）</li>}
            </ul>
          </div>
        )}

        {tab === 'health' && (
          <div className="bg-white border border-[#d5dee3]">
            <div className="px-4 py-3 border-b border-[#e6ecef] font-medium">系统自检</div>
            <ul className="divide-y divide-[#eef2f4]">
              {(health?.checks || []).map((c) => (
                <li key={c.name} className="px-4 py-3 flex gap-4 text-sm">
                  <span className="w-40 mono" style={{ color: c.ok ? 'var(--accent)' : 'var(--crit)' }}>
                    {c.ok ? 'OK' : c.level.toUpperCase()}
                  </span>
                  <span className="w-48 font-medium">{c.name}</span>
                  <span className="text-[var(--muted)] truncate">{c.detail}</span>
                </li>
              ))}
            </ul>
          </div>
        )}
      </main>
    </div>
  )
}

export default function App() {
  const [authed, setAuthed] = useState(null)

  useEffect(() => {
    api('/api/auth/me')
      .then(() => setAuthed(true))
      .catch(() => setAuthed(false))
  }, [])

  if (authed === null) {
    return <div className="min-h-full grid place-items-center text-[var(--muted)]">加载中…</div>
  }
  return authed ? <Dashboard /> : <Login onOk={() => setAuthed(true)} />
}
