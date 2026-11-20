import { Suspense } from 'react';
import type { Background } from '../../api/types';
import { backgroundLine } from '../../lib/background';
import { useGatewayBack } from '../../lib/gateway';
import { LazyOopsAcorn } from '../../ui/LazyOopsAcorn';
import { Shown } from '../../ui/Live';
import { GatewayBack } from './lazyMoments';

/** The model's background pass, as Home's For you and Review show it (lib/background.ts), with the gateway's character
 *  (phase 17 spec 3.7): the oops face while it is away, and the win hop with "back" once when it returns. */
export function BackgroundLine({ bg, className, testId }: { bg: Background; className: string; testId: string }) {
  const back = useGatewayBack(bg);
  const text = backgroundLine(bg);
  if (!text) return null;
  return (
    <Shown className={`${className} bg-line`} data-testid={testId}>
      {bg.waiting && <LazyOopsAcorn />}
      {back && !bg.waiting ? <Suspense fallback={text}><GatewayBack /></Suspense> : text}
    </Shown>
  );
}
