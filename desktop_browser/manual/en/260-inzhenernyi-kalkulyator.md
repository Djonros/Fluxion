# Engineering calculator
<!-- раздел: Расчёты | инструмент: calc -->
## What it is for

A quick check of parts by formulas: welds, bolts, beams, a whole trailer frame, compressed columns, sheet bending, shafts and axles, pins and lugs, hydraulic cylinders, trailer axle loads. The result is a safety factor and a verdict: «factor 1.1 — too low: an 8 mm leg gives 1.6». The numbers are computed by formulas, not by the model.

## How to start

Menu **Design → «Engineering calculator…»**. In the chat: `/расчёт` and a question in your own words. By voice: «Расчёт, …» or «посчитай балку …».

## Step by step

1. Choose a tab: «Welds», «Bolts», «Beams», «Frame», «Columns», «Bending», «Shafts», «Pins», «Hydraulic cylinder» or «Axle loads» (the tabs are named in Russian: «Пальцы», «Гидроцилиндр», «Развесовка»).
2. Fill in the fields with an asterisk. Units may be typed: `5 тс`, `500 кгс`, `1,5 м`, `300 Н·м`. A number without a unit is in the units of the field: kN, mm, N·m.
3. «Method» on the «Welds» and «Bolts» tabs: **mechanical engineering** — the factor against the yield strength (required factor [n] = 1.5), or **SP 16.13330** — design resistances (enter design loads).
4. For trailers set the dynamic factor kd = 1.5–2: the load is multiplied by it.
5. «Beams»: supports separated by «;» from the left end — `1500; 5500` (trailer axles), `заделка 0` — a cantilever. Loads: `20 кН в 1500; 5 кН/м; 3 кН/м от 0 до 2000`. «Beams share the load» — 2 for two side members. «Bracing of the compressed flange every» — the cross member pitch: the stability of a channel or an I-beam is checked. «Bearing length» — the local stress in the web under a load and above a support.
6. «Frame» — the whole trailer frame: length and width over the side members, axles `2800; 3600`, cross members `0; 1000; 2000; …`, the length of the V drawbar, profiles of the side members, cross members and drawbar. Joints — **rigid** (cross members welded in) or **pinned** (bolted). The load on the platform — the whole of it, in tf or kN; point loads — `1 тс в 2000` (in the middle), `500 кгс в 3000 слева` (on the left). For a single-axle trailer tick «The coupling carries a vertical load». «Weld leg of the cross members» — the check of the welds of the cross members to the side members.
7. «Columns» — a compressed member: the force, the length, the end fixing (pins, cantilever, fixed ends), the profile; a bending moment may be added.
8. «Pins» — a joint on a pin: the force, the pin diameter, **double shear** (a pin in a fork of two cheeks) or **single shear**, the thickness of the lug, of the fork cheeks and the gap. Checked are the shear and bending of the pin, bearing, the tension of the lug at the hole and the tear-out behind it. The lug width and the edge distance default to 2.5·d and half the width. Hints: the next pin diameter, the lug thickness.
9. «Hydraulic cylinder» — the piston and rod diameters, the pressure (MPa or bar: 16 MPa = 160 bar). Push and pull forces; with a load — the pressure it needs; with the length between the pins at full extension — the buckling of the rod (required factor 3.5). Hints — the nearest piston and rod by GOST 6540.
10. «Axle loads» — the masses of the frame and the cargo with their centres of gravity from the coupling, the axles separated by «;» from the coupling, the permissible loads. A semitrailer or a centre-axle trailer — «The coupling carries a load» = yes; a drawbar trailer with a turntable front axle — no. The axles of a group share the load equally (a balancer). With the track and the height of the centre of gravity — the rollover threshold B/(2h) against the required lateral acceleration (0.4 g). The hint — within which limits to keep the centre of gravity of the cargo.
11. Press **«Calculate»**.
12. In the chat: `/расчёт палец 40 двухсрезный, проушина 30, 12 тс`, `/расчёт цилиндр 100 на 50, 160 бар, ход 1800, груз 10 т`, `/расчёт рама 6 м на осях 1500 и 4500, швеллер 16, поперечины труба 60х40х3 через 1500, груз 3 тс, kд 1,5`. If data is missing, Fluxion asks. Then the calculator opens with this calculation.

## What you get

The verdict with the factor and a colour, the formulas with the numbers in them, all checks and what to change: the next profile of the range, a bigger leg or diameter, another steel grade, a longer key — every suggestion is calculated too. For beams — the moment diagram, deflection and support reactions. For the frame — a top view with every member coloured by its factor, the forces in each member (moment, shear, torsion), the reactions of the axles and the coupling, the deflections of the side members, stability and the welds of the joints. **A report in Excel or PDF** with the inputs and formulas — in Fluxion Pro.

## If it does not work

- «Unknown material»: the yield and ultimate strengths come from the «Materials catalog». Add your grade there with the fields `strength` and `elastic`.
- Profiles: channel 5–30 (GOST 8240), I-beam 10–30 (GOST 8239), rectangular tube `80х40х3`, round tube `57х3,5`, bent channel `100х50х4`, flat bar `100х10`, round bar `40`. «плашмя» (flat) — bending in the other plane.
- «The frame is not fixed»: on one axle the trailer tips over — tick that the coupling carries a load.
- Not calculated: warping torsion of channels (for channel cross members the real factor is lower), the spatial work of a body with sides, fatigue of welded joints, impacts and local dents. A structure that matters is checked by an engineer.
- The calculation results and the report are in Russian.
