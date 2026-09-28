import { Help } from './Help'

/* Said beside any price this app worked out itself.

   When almost nobody is trading a contract, the gap between the best bid and
   the best offer is too wide for its midpoint to mean anything, and the
   broker's platform does not show that midpoint either. Those legs are priced
   from their volatility and the price of what they are written on, kept
   inside the bid and ask — and this says so, so an estimate is never read as
   a quote. */
export function Estimate({ compact = false }: { compact?: boolean }) {
  return (
    <Help
      title="Low volume estimate"
      body={
        'Hardly anyone is trading this contract right now, so the gap between the bid and the ask ' +
        'is too wide to take a midpoint from. Its price is worked out instead from its volatility ' +
        'and the price of what it is written on, and kept between the bid and the ask. It can ' +
        'differ from tastytrade by a few dollars; it will not be wildly off.'
      }
    >
      <span className="ml-1.5 inline-block whitespace-nowrap rounded border border-tested/40 bg-tested-soft px-1.5 py-px align-middle text-[12px] font-medium text-tested">
        {compact ? 'est.' : 'low volume estimate'}
      </span>
    </Help>
  )
}
