import os, json, time, urllib.request, urllib.error
cfg=json.loads(os.environ["CODEBUDDY_MCP_CONFIG"]); nd=cfg["mcpServers"]["neodata"]; URL=nd["url"]; H=nd["headers"]
def post(s,m,p,i,nf=False):
    b={"jsonrpc":"2.0","method":m}
    if not nf: b["id"]=i
    if p is not None: b["params"]=p
    d=json.dumps(b).encode()
    r=urllib.request.Request(URL,data=d,method="POST")
    r.add_header("Content-Type","application/json"); r.add_header("Accept","application/json, text/event-stream")
    for k,v in H.items(): r.add_header(k,v)
    if s: r.add_header("Mcp-Session-Id",s)
    try: resp=urllib.request.urlopen(r,timeout=120)
    except urllib.error.HTTPError as e: return s,e.code,e.read().decode("utf-8","replace")
    sid=resp.headers.get("Mcp-Session-Id"); raw=resp.read().decode(); ct=resp.headers.get("Content-Type","")
    if "text/event-stream" in ct:
        objs=[]
        for ln in raw.splitlines():
            ln=ln.strip()
            if ln.startswith("data:"):
                c=ln[5:].strip()
                if c:
                    try: objs.append(json.loads(c))
                    except: pass
        return sid,200,(objs[-1] if objs else None)
    try: return sid,200,(json.loads(raw) if raw.strip() else None)
    except: return sid,200,raw
def call():
    s,_,_=post(None,"initialize",{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"p","version":"1.0"}},1)
    post(s,"notifications/initialized",{},None,nf=True)
    p={"name":"futures_rank","arguments":{"variety_codes":"LH.DCE","top_n":20,"trading_date":"20261030","limit":1000,"output_fields":"variety_code,contract_code,indicator_code,indicator_volume,change_volume,end_date"}}
    sid,code,resp=post(s,"tools/call",p,2)
    if code!=200: return code,str(resp)[:60]
    txt=resp.get("result",{}).get("content",[{}])[0].get("text","")
    o=json.loads(txt); return o.get("status"),"nrows="+str(len(o.get("result",[])))
GAP=150
log=open("raw_hist/_probe.log","a",encoding="utf-8")
for i in range(1,7):
    t=time.strftime("%H:%M:%S")
    try:
        st,info=call()
    except Exception as e:
        st,info="EXC",str(e)[:60]
    print("%s call%d -> %s %s"%(t,i,st,info),flush=True)
    log.write("%s call%d -> %s %s\n"%(t,i,st,info)); log.flush()
    if i<6:
        time.sleep(GAP)
