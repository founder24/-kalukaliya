const l=a=>a?.role==="staff"||a?.role==="admin",t=(a,i)=>l(a)&&(a.role==="admin"||a.capabilities===null||Array.isArray(a.capabilities)&&a.capabilities.includes(i));export{t as c,l as i};
