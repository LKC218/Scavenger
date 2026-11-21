/** 原作模型独立接入防线，不改V003已通过的十八份运行源码。 */
import{DefenseBattlefield as LegacyBattlefield}from'/legacy-defense/尸群表现.mjs';
import{NativeActors}from'/native/合批.mjs';
export class DefenseBattlefield extends LegacyBattlefield{
 async loadActors(manifest,progress){progress('正在准备Walker与Runner原骨架及九段原生动作…');this.actors=new NativeActors(this.scene);await this.actors.load(this.renderer,manifest.actors);}
}
