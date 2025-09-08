{
  "relOp": "LogicalProject",
  "project": [
    {
      "kind": "INPUT_REF",
      "index": 77,
      "name": "$77",
      "type": "VARCHAR"
    }
  ],
  "id": "4",
  "inputs": [
    {
      "relOp": "LogicalFilter",
      "condition": {
        "kind": "AND",
        "operator": "AND",
        "type": "BOOLEAN",
        "operands": [
          {
            "kind": "EQUALS",
            "operator": "=",
            "type": "BOOLEAN",
            "operands": [
              {
                "kind": "INPUT_REF",
                "index": 48,
                "name": "$48",
                "type": "VARCHAR"
              },
              {
                "kind": "LITERAL",
                "value": "Fellwar Stone",
                "type": "VARCHAR",
                "nullable": false,
                "precision": -1
              }
            ]
          },
          {
            "kind": "EQUALS",
            "operator": "=",
            "type": "BOOLEAN",
            "operands": [
              {
                "kind": "INPUT_REF",
                "index": 75,
                "name": "$75",
                "type": "VARCHAR"
              },
              {
                "kind": "LITERAL",
                "value": "Japanese",
                "type": "VARCHAR",
                "nullable": false,
                "precision": -1
              }
            ]
          },
          {
            "kind": "NOT",
            "operator": "NOT",
            "type": "BOOLEAN",
            "operands": [
              {
                "kind": "IS_NULL",
                "operator": "IS NULL",
                "type": "BOOLEAN",
                "operands": [
                  {
                    "kind": "INPUT_REF",
                    "index": 77,
                    "name": "$77",
                    "type": "VARCHAR"
                  }
                ]
              }
            ]
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
                "index": 76,
                "name": "$76",
                "type": "VARCHAR"
              },
              {
                "kind": "INPUT_REF",
                "index": 62,
                "name": "$62",
                "type": "VARCHAR"
              }
            ]
          },
          "id": "2",
          "inputs": [
            {
              "relOp": "LogicalTableScan",
              "table": "cards",
              "id": "0",
              "inputs": []
            },
            {
              "relOp": "LogicalTableScan",
              "table": "set_translations",
              "id": "1",
              "inputs": []
            }
          ]
        }
      ]
    }
  ]
}