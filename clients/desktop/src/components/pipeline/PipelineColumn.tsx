// One board lane (Needs your go-ahead / Queued / Working now / Shipped) with its
// labelled header and card list. Purely presentational; the parent owns the
// cards it renders as children.
export function PipelineColumn({
  label,
  count,
  lane,
  children,
}: {
  label: string;
  count: number;
  lane: "needs" | "queued" | "working" | "shipped";
  children: React.ReactNode;
}) {
  return (
    <section
      className="alfred-pipeline__column"
      data-lane={lane}
      aria-label={`${label} (${count})`}
    >
      <div className="alfred-pipeline__column-head">
        <span>{label}</span>
        <small>{count}</small>
      </div>
      <div className="alfred-pipeline__cards motion-rise">{children}</div>
    </section>
  );
}
