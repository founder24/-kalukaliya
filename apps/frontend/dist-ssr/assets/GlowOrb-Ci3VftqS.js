import{jsxs as c,Fragment as f,jsx as o}from"react/jsx-runtime";function p({color:a,size:r,x:n,y:s,blur:l,opacity:i=.18,animRange:t=30,duration:m=14}){const e=`glowOrbFloat-${t}`;return c(f,{children:[o("style",{children:`
        @keyframes ${e} {
          0%   { transform: translate(0, 0)                       scale(1);    }
          33%  { transform: translate(${t}px, -${t/2}px) scale(1.08); }
          66%  { transform: translate(-${t/2}px, ${t}px) scale(0.96); }
          100% { transform: translate(0, 0)                       scale(1);    }
        }
        @media (prefers-reduced-motion: reduce) {
          .glow-orb-anim { animation: none !important; }
        }
      `}),o("div",{className:"absolute rounded-full pointer-events-none glow-orb-anim",style:{width:r,height:r,left:n,top:s,background:a,filter:`blur(${l}px)`,opacity:i,animation:`${e} ${m}s ease-in-out infinite`}})]})}export{p as G};
