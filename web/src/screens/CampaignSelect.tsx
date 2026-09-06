import { useEffect, useState } from 'react'
import { api, type CampaignSize, type CampaignSummary } from '../api/client'

const SIZE_LABELS: Record<CampaignSize, string> = {
  one_shot: 'One-Shot',
  short_arc: 'Short Arc',
  full: 'Full Campaign',
}

export default function CampaignSelect({
  onStart,
  onBack,
  starting,
  startError,
}: {
  onStart: (campaignId: string) => void
  onBack: () => void
  starting: boolean
  startError: string | null
}) {
  const [campaigns, setCampaigns] = useState<CampaignSummary[]>([])
  const [campaignId, setCampaignId] = useState('')
  const [loadError, setLoadError] = useState<string | null>(null)

  useEffect(() => {
    api
      .listCampaigns()
      .then((list) => {
        setCampaigns(list)
        if (list.length === 1) setCampaignId(list[0].id)
      })
      .catch((err: unknown) => setLoadError(err instanceof Error ? err.message : String(err)))
  }, [])

  if (loadError) {
    return <div className="wizard-error">Could not load campaigns: {loadError}</div>
  }

  return (
    <div className="wizard">
      <h1>Choose a Campaign</h1>
      <div className="campaign-list">
        {campaigns.map((c) => (
          <label
            key={c.id}
            className={`campaign-card ${campaignId === c.id ? 'selected' : ''}`}
          >
            <input
              type="radio"
              name="campaign"
              checked={campaignId === c.id}
              onChange={() => setCampaignId(c.id)}
            />
            <div>
              <strong>{c.title}</strong>
              <span className="campaign-size">{SIZE_LABELS[c.size]}</span>
              <p>{c.description}</p>
            </div>
          </label>
        ))}
      </div>
      <button type="button" onClick={onBack} disabled={starting}>
        Back
      </button>
      <button
        type="button"
        disabled={campaignId === '' || starting}
        onClick={() => onStart(campaignId)}
      >
        {starting ? 'Starting...' : 'Start Adventure'}
      </button>
      {startError && <p className="wizard-error">{startError}</p>}
    </div>
  )
}
