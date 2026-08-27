import { useEffect, useRef } from "react";
import * as echarts from "echarts";

interface ChartProps {
  option: echarts.EChartsOption;
  height?: number;
  ariaLabel: string;
}

export default function Chart({ option, height = 300, ariaLabel }: ChartProps) {
  const elementRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const element = elementRef.current;
    if (!element) return;
    const chart = echarts.init(element, undefined, { renderer: "canvas" });
    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    chart.setOption(reducedMotion ? { ...option, animation: false } : option, { notMerge: true });
    const resize = () => chart.resize();
    const observer = new ResizeObserver(resize);
    observer.observe(element);
    window.addEventListener("resize", resize);
    return () => {
      observer.disconnect();
      window.removeEventListener("resize", resize);
      chart.dispose();
    };
  }, [option]);

  return <div ref={elementRef} className="chart" style={{ height }} role="img" aria-label={ariaLabel} />;
}
