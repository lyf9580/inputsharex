import java.lang.reflect.Method;
public class PointerParserTest {
    public static void main(String[] args) throws Exception {
        Method parse = InputShareXPointerProbe.class.getDeclaredMethod("findPoint", String.class, int.class);
        parse.setAccessible(true);
        String point = "[hoveringPointers=[Pointer(id=0, MOUSE) at (123.5, 456.0)]]";
        for (String fixture : new String[] {
            "mDeviceStates=415:" + point,
            "mDeviceStates=-1:[hoveringPointers=[Pointer(id=0, MOUSE) at (9, 9)]]\n 415:" + point,
            "mDeviceStates=\n DeviceId(415):" + point,
            "mDeviceStates=12:[hoveringPointers=[]]\n DeviceId(415):" + point + "\n DeviceId(9):[hoveringPointers=[]]"
        }) {
            float[] xy = (float[])parse.invoke(null, fixture, 415);
            if (xy == null || xy[0] != 123.5f || xy[1] != 456f) throw new AssertionError(fixture);
            if (parse.invoke(null, fixture, 999) != null) throw new AssertionError("Wrong device");
        }
        System.out.println("4 pointer dump format regressions passed");
    }
}
