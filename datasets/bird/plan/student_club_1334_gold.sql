{
  "relOp": "LogicalProject",
  "project": [
    {
      "kind": "INPUT_REF",
      "index": 11,
      "name": "$11",
      "type": "VARCHAR"
    },
    {
      "kind": "INPUT_REF",
      "index": 13,
      "name": "$13",
      "type": "VARCHAR"
    }
  ],
  "id": "4",
  "inputs": [
    {
      "relOp": "LogicalFilter",
      "condition": {
        "kind": "EQUALS",
        "operator": "=",
        "type": "BOOLEAN",
        "operands": [
          {
            "kind": "INPUT_REF",
            "index": 4,
            "name": "$4",
            "type": "VARCHAR"
          },
          {
            "kind": "LITERAL",
            "value": "President",
            "type": "VARCHAR",
            "nullable": false,
            "precision": -1
          }
        ]
      },
      "variableset": "[]",
      "id": "3",
      "inputs": [
        {
          "relOp": "LogicalJoin",
          "joinType": "inner",
          "condition": {
            "kind": "EQUALS",
            "operator": "=",
            "type": "BOOLEAN",
            "operands": [
              {
                "kind": "INPUT_REF",
                "index": 7,
                "name": "$7",
                "type": "INTEGER"
              },
              {
                "kind": "INPUT_REF",
                "index": 9,
                "name": "$9",
                "type": "INTEGER"
              }
            ]
          },
          "id": "2",
          "inputs": [
            {
              "relOp": "LogicalTableScan",
              "table": "member",
              "id": "0",
              "inputs": []
            },
            {
              "relOp": "LogicalTableScan",
              "table": "zip_code",
              "id": "1",
              "inputs": []
            }
          ]
        }
      ]
    }
  ]
}