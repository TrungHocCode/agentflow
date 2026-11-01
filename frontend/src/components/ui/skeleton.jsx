import { cn } from "cn"

function Skeleton({
  className,
  ...props
}) {
  return (
    <div
      data-slot="skeleton"
      aria-hidden="true"
      className={cn("skeleton-block", className)}
      {...props}
    />
  )
}

export { Skeleton }
